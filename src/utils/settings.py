"""Application settings — Qt-free.

Storage is a JSON object in the user's config dir:

    %APPDATA%\\EXE-fingerprinter\\settings.json   (Windows)
    ~/.config/EXE-fingerprinter/settings.json (Linux)
    ~/Library/Application Support/EXE-fingerprinter/settings.json (macOS)

A one-time migration imports the legacy QSettings INI file
(``~/.config/ACCELA/Tachibana Labs/ACCELA.conf``) if it exists, so users do not
lose their depot keys, Hubcap keys and preferences on upgrade. Nothing from
the old file that the tool no longer understands is kept silently — it is
merged key-by-key.
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Tuple

logger = logging.getLogger(__name__)

ORG_NAME = "Tachibana Labs"
APP_NAME = "ACCELA"          # legacy QSettings location
LEGACY_APP = "ACCELA"
NEW_APP = "EXE-fingerprinter"

_settings_local = threading.local()
_migration_attempted = False


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / NEW_APP


def _settings_path() -> Path:
    return _config_dir() / "settings.json"


def _legacy_ini() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / ORG_NAME / "ACCELA.ini"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / f"{ORG_NAME}.ACCELA.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / ORG_NAME / "ACCELA.conf"


def _parse_qsettings_ini(path: Path) -> dict[str, Any]:
    """Best-effort read of a Qt QSettings INI into a flat key/value dict."""
    data: dict[str, Any] = {}
    section = ""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return data
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#", "@")):
            # Qt stores lists/blobs as @Variant(...); skip them, keep scalars.
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            # Qt writes top-level settings under a default "General" group;
            # the app reads them unprefixed, so strip that group on load.
            if section.lower() == "general":
                section = ""
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace("\\\\", "\\").replace('\\"', '"')
        full = f"{section}/{key}" if section else key
        low = value.lower()
        if low in ("true", "yes", "on"):
            parsed: Any = True
        elif low in ("false", "no", "off"):
            parsed = False
        else:
            try:
                parsed = int(value)
            except ValueError:
                try:
                    parsed = float(value)
                except ValueError:
                    parsed = value
        data[full] = parsed
    return data


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Small stand-in for the old QSettings facade (value/setValue/remove/sync)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _settings_path()
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (ValueError, OSError) as exc:
                logger.warning(f"Settings file {self.path} unreadable ({exc}); starting fresh")
        # Undo the old "General/" prefix on keys migrated before the fix.
        stripped_data: dict[str, Any] = {}
        for key, value in data.items():
            low = key.lower()
            if low.startswith("general/"):
                stripped_data[key.split("/", 1)[1]] = value
            else:
                stripped_data[key] = value

        legacy = _legacy_ini()
        global _migration_attempted
        if legacy.exists() and not _migration_attempted:
            _migration_attempted = True
            try:
                merged = _parse_qsettings_ini(legacy)
                added = 0
                for key, value in merged.items():
                    if key not in stripped_data:
                        stripped_data[key] = value
                        added += 1
                if added:
                    logger.info(f"Imported {added} legacy settings from {legacy}")
                    self._data = stripped_data
                    self.sync()
            except Exception as exc:
                logger.debug(f"Legacy settings migration skipped: {exc}")
        self._data = stripped_data

    def value(self, key: str, defaultValue: Any = None, type: Any = None) -> Any:
        val = self._data.get(key, defaultValue)
        if val is None:
            return defaultValue
        if type is bool or (type is None and isinstance(defaultValue, bool)):
            if val is None:
                return bool(defaultValue) if defaultValue is not None else False
            return _to_bool(val)
        if type in (int, float, str):
            try:
                return type(val)
            except (TypeError, ValueError):
                return defaultValue
        return val

    def setValue(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._dirty = True

    def setValueQuick(self, key: str, value: Any) -> None:
        self.setValue(key, value)
        self.sync()

    def remove(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._dirty = True
        # Also drop any literal "key/*" children, matching QSettings(group) removal.
        prefix = key.rstrip("/") + "/"
        for k in list(self._data):
            if k.startswith(prefix):
                del self._data[k]
                self._dirty = True

    def contains(self, key: str) -> bool:
        return key in self._data

    def allKeys(self) -> list[str]:
        return list(self._data)

    def sync(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._dirty = False
        except OSError as exc:
            logger.warning(f"Could not save settings to {self.path}: {exc}")

    def fileName(self) -> str:
        return str(self.path)


def get_settings() -> Settings:
    """Return the thread-local settings object (cached)."""
    if not hasattr(_settings_local, "instance"):
        instance = Settings()
        if instance.value("exe_fingerprinter_initialized") is None:
            instance.setValue("exe_fingerprinter_initialized", True)
            instance.sync()
        _settings_local.instance = instance
    return _settings_local.instance


def is_canary_welcome_needed() -> bool:
    """Legacy helper kept for source compatibility; the TUI has no onboarding slideshow."""
    return False
