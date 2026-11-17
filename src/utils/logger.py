"""Stdlib-only logging for EXE-fingerprinter.

Writes a timestamped, sanitized log file under the app's data dir. Nothing is
printed to the terminal: the TUI owns stdout/stderr and a stray log line
would corrupt the raw-terminal display. Enable console output only with
``setup_logging(console=True)`` for headless/debug runs.
"""

import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.helpers import get_base_path

APP_NAME = "exe_fingerprinter"
MAX_PREVIOUS_LOGS = 3

logger = logging.getLogger(__name__)

_log_dir = get_base_path() / "logs"
_current_log_name: Optional[str] = None


class SensitiveDataFilter(logging.Filter):
    """Redacts keys/passwords/tokens anywhere in a record before it is written."""

    PATTERNS = [
        (re.compile(r"smm_[a-zA-Z0-9_-]{16,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
        (re.compile(r"Bearer\s+[a-zA-Z0-9._~+/-]+", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
        (
            re.compile(
                r"(api_key|apikey|morrenus_api_key|access_token|secret_key|private_key|token)[=:\s]+([\"']?)([^\s,\"'&]+)([\"']?)",
                re.IGNORECASE,
            ),
            r"\1=\2[REDACTED_KEY]\4",
        ),
        (
            re.compile(
                r"(password|steam_password|passwd)[=:\s]+([\"']?)([^\s,\"'&]+)([\"']?)",
                re.IGNORECASE,
            ),
            r"\1=\2[REDACTED_PASSWORD]\4",
        ),
        (re.compile(r"(-password|-pass)\s+([^\s]+)", re.IGNORECASE), r"\1 [REDACTED_PASSWORD]"),
    ]

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        for pattern, replacement in cls.PATTERNS:
            text = pattern.sub(replacement, text)
        try:
            from utils.settings import get_settings

            settings = get_settings()
            if settings:
                for key, replacement in (
                    ("morrenus_api_key", "[REDACTED_API_KEY]"),
                    ("steam_password", "[REDACTED_PASSWORD]"),
                    ("exefp_api_password", "[REDACTED_PASSWORD]"),
                ):
                    secret = settings.value(key, "", type=str)
                    if secret and len(str(secret)) > 3 and str(secret) in text:
                        text = text.replace(str(secret), replacement)
        except Exception:
            pass
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            record.msg = self.sanitize_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (self.sanitize_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    self.sanitize_text(a) if isinstance(a, str) else a for a in record.args
                )
        return True


class SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return SensitiveDataFilter.sanitize_text(message)


def get_log_path() -> Path:
    """Path of a timestamped log file, reusing an active one if it exists."""
    global _current_log_name, _log_dir
    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _log_dir = Path(os.environ.get("TEMP", os.getcwd())) / "logs" / APP_NAME
        _log_dir.mkdir(parents=True, exist_ok=True)

    if _current_log_name and (_log_dir / _current_log_name).exists():
        return _log_dir / _current_log_name

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{APP_NAME}_{timestamp}"
    counter = 1
    while True:
        log_name = f"{base_name}.log" if counter == 1 else f"{base_name}_{counter}.log"
        log_path = _log_dir / log_name
        if not log_path.exists():
            break
        counter += 1
    _current_log_name = log_name
    return log_path


def cleanup_old_logs() -> None:
    global _log_dir
    _log_dir = get_base_path() / "logs"
    if not _log_dir.exists():
        return
    log_files = [
        f for f in _log_dir.glob("*.log")
        if f.is_file() and (f.name.startswith(f"{APP_NAME}_") or f.name.startswith("accela_") or f.name.startswith("exe_fingerprinter_"))
    ]
    if not log_files:
        return
    log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    for old_log in log_files[MAX_PREVIOUS_LOGS:]:
        try:
            old_log.unlink()
        except OSError:
            pass


def setup_logging(level: int = logging.INFO, console: bool = False) -> logging.Logger:
    """Configure root logging for the app.

    ``console`` stays False by default: the TUI owns the terminal, and a raw
    log line mid-render corrupts the display. Use console=True only for
    headless test/CI runs.
    """
    cleanup_old_logs()

    formatter = SanitizingFormatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)-32s | %(message)s",
        datefmt="%H:%M:%S",
    )
    formatter.sanitizer = SensitiveDataFilter()  # noqa: B010 - kept for clarity

    handlers: list[logging.Handler] = []

    try:
        log_path = get_log_path()
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, mode="a", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SensitiveDataFilter())
        handlers.append(file_handler)
        print(f"Logging to: {log_path}", file=sys.stderr) if console else None
    except OSError as exc:
        print(f"Could not open a log file, logging to stderr only: {exc}", file=sys.stderr)

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(SensitiveDataFilter())
        handlers.append(stream_handler)

    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False

    # Keep noisy third-party libraries out of the log unless debugging.
    for noisy in ("urllib3", "requests", "chardet", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING if level > logging.DEBUG else level)

    return root
