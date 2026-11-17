"""
SteamDB Scraper & Rollback Metadata Retriever for EXE-fingerprinter.

Features:
- Byparr integration for automated Cloudflare Turnstile bypass (using stealth Camoufox).
- Fast headless DOM resolution for dynamic SteamDB patchnotes tables.
- Robust HTML parsers for:
    * App patchnotes overview (/app/<appid>/patchnotes/) -> Build IDs, dates, titles
    * Patch detail pages (/patchnotes/<buildid>/) -> Depot IDs, Manifest IDs
- Data endpoints for latest build, depot info, base game depot, folder structure,
  game image, and AppID/name wise search.
- SteamDB App Parser integration (YAML output for SLSsteam DLC/AdditionalApps).
"""

import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import logging
import concurrent.futures
import email.utils
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

import requests

# curl_cffi and bs4 are heavy (about 0.1s warm, close to a second on a cold
# start) and only needed when SteamDB is actually queried. The main window
# imports this module while it is being built, so both load on first use.
class _LazyModule:
    """Imports a module on first use; falsy when it is not installed."""

    def __init__(self, name: str):
        self._name = name
        self._mod = None
        self._tried = False

    def _load(self):
        if not self._tried:
            self._tried = True
            try:
                import importlib

                self._mod = importlib.import_module(self._name)
            except ImportError:
                self._mod = None
        return self._mod

    def __bool__(self) -> bool:
        return self._load() is not None

    def __getattr__(self, attr):
        mod = self._load()
        if mod is None:
            raise AttributeError(f"{self._name} is not installed")
        return getattr(mod, attr)


cffi_requests = _LazyModule("curl_cffi.requests")


def BeautifulSoup(*args, **kwargs):  # noqa: N802 - keeps the bs4 name for callers
    from bs4 import BeautifulSoup as _BeautifulSoup

    return _BeautifulSoup(*args, **kwargs)


from utils.settings import get_settings
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

BYPARR_DEFAULT_PORT = 8191
BYPARR_DEFAULT_URL = f"http://127.0.0.1:{BYPARR_DEFAULT_PORT}"
STEAMDB_BASE_URL = "https://steamdb.info"

# Fast-path race profiles for Cloudflare bypass via direct TLS impersonation
FAST_RACE_PROFILES = [
    {
        "name": "Chrome 152 (Windows)",
        "impersonate": "chrome152",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
    },
    {
        "name": "Chrome 152 (macOS)",
        "impersonate": "chrome152_macos",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    },
    {
        "name": "Safari 17.0 (macOS)",
        "impersonate": "safari17_0",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 270 (macOS)",
        "impersonate": "safari270",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 180 (iOS)",
        "impersonate": "safari180_ios",
        "headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
]


class ByparrManager:
    """Manages the background Byparr helper process for Cloudflare clearance."""

    _process: Optional[subprocess.Popen] = None
    _managed: bool = False

    @classmethod
    def get_service_url(cls) -> str:
        try:
            settings = get_settings()
            url = settings.value("byparr_url", BYPARR_DEFAULT_URL, type=str).rstrip("/")
            return url or BYPARR_DEFAULT_URL
        except Exception:
            return BYPARR_DEFAULT_URL

    @classmethod
    def get_auth_header(cls) -> str:
        try:
            settings = get_settings()
            return settings.value("byparr_auth_header", "", type=str).strip()
        except Exception:
            return ""

    @classmethod
    def is_custom_service(cls) -> bool:
        try:
            settings = get_settings()
            url = settings.value("byparr_url", "", type=str).strip()
            return bool(url and url.rstrip("/") != BYPARR_DEFAULT_URL.rstrip("/"))
        except Exception:
            return False

    @classmethod
    def is_local_enabled(cls) -> bool:
        if cls.is_custom_service():
            return False
        if not cls.find_byparr_dir():
            return False
        try:
            return get_settings().value("byparr_enabled", True, type=bool)
        except Exception:
            return True

    @classmethod
    def is_available(cls) -> bool:
        if cls.is_custom_service():
            return True
        return cls.is_local_enabled()

    @classmethod
    def is_running(cls) -> bool:
        if cls.is_custom_service():
            try:
                import urllib.parse
                url = cls.get_service_url()
                parsed = urllib.parse.urlparse(url)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.7)
                res = s.connect_ex((host, port))
                s.close()
                return res == 0
            except Exception:
                return False

        try:
            port = BYPARR_DEFAULT_PORT
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            res = s.connect_ex(("127.0.0.1", port))
            s.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def find_uv_path(cls) -> Optional[str]:
        uv = shutil.which("uv")
        if uv:
            return uv
        candidates = [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                return str(c)
        return None

    @classmethod
    def find_byparr_dir(cls) -> Optional[Path]:
        candidates = [
            Path.home() / ".local" / "share" / "ACCELA" / "byparr",
            get_base_path() / "byparr",
            Path("/tmp/byparr_test"),
            Path.home() / "Byparr",
        ]
        for c in candidates:
            if c.exists() and (c / "main.py").exists():
                return c
        return None

    @classmethod
    def _rotate_byparr_log(cls, log_path: Path, max_sessions: int = 5) -> None:
        try:
            if not log_path.exists() or log_path.stat().st_size == 0:
                return
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            pattern = r"(?=(?:\r?\n|^)---\s*Byparr Session Started by\b)"
            sessions = [s for s in re.split(pattern, content) if s.strip()]
            keep_count = max(1, max_sessions - 1)
            if len(sessions) >= max_sessions:
                trimmed_content = "\n".join(s.strip() for s in sessions[-keep_count:]) + "\n"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(trimmed_content)
                logger.debug(f"[SteamDB] Pruned byparr.log from {len(sessions)} to {keep_count} sessions.")
        except Exception as e:
            logger.debug(f"[SteamDB] Error rotating byparr.log: {e}")

    @classmethod
    def ensure_running(cls) -> bool:
        if get_settings().value("disable_steamdb_scraping", False, type=bool):
            logger.info("[SteamDB] Scraping disabled in Testing settings; will not start Byparr.")
            return False

        if cls.is_custom_service():
            return cls.is_running()

        if not cls.is_local_enabled():
            logger.info("[SteamDB] Local Byparr is disabled by user.")
            return False

        if cls.is_running():
            return True

        byparr_dir = cls.find_byparr_dir()
        if not byparr_dir:
            logger.warning("[SteamDB] Byparr service not running and local repository not found.")
            return False

        try:
            env = os.environ.copy()
            env.pop("LD_LIBRARY_PATH", None)
            env.pop("LD_PRELOAD", None)
            local_bin = str(Path.home() / ".local" / "bin")
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = f"{local_bin}:{cargo_bin}:{env.get('PATH', '')}"
            env["INVPW_TRUE_HEADLESS"] = "1"
            env["PORT"] = str(BYPARR_DEFAULT_PORT)
            env["HOST"] = "127.0.0.1"
            env["EXE_FINGERPRINTER_PID"] = str(os.getpid())
            env["ACCELA_PID"] = str(os.getpid())

            uv_path = cls.find_uv_path()
            runner_script = byparr_dir / "exe_fingerprinter_runner.py"
            if not runner_script.exists():
                runner_script = byparr_dir / "accela_runner.py"
            target_script = runner_script.name if runner_script.exists() else "main.py"

            if uv_path and (byparr_dir / "uv.lock").exists():
                cmd = [uv_path, "run", "python", target_script]
            else:
                cmd = ["python3", target_script]

            def _preexec():
                try:
                    import ctypes
                    libc = ctypes.CDLL("libc.so.6")
                    libc.prctl(1, signal.SIGTERM)
                except Exception as _silent_err:
                    logger.debug("[steamdb_scraper] ensure_running: ignored %s", _silent_err, exc_info=False)
                try:
                    os.setpriority(os.PRIO_PROCESS, 0, 10)
                except OSError as e:
                    logger.debug(f"[SteamDB] Could not lower the helper priority: {e}")

            logger.info(f"[SteamDB] Starting background Byparr process in {byparr_dir} (EXE-FINGERPRINTER PID={os.getpid()})...")
            log_dir = get_base_path() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            byparr_log_path = log_dir / "byparr.log"
            cls._rotate_byparr_log(byparr_log_path, max_sessions=5)
            byparr_logfile = open(byparr_log_path, "a", encoding="utf-8")
            byparr_logfile.write(
                f"\n--- Byparr Session Started by EXE-FINGERPRINTER (PID={os.getpid()}) at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
            )
            byparr_logfile.flush()
            cls._process = subprocess.Popen(
                cmd,
                cwd=str(byparr_dir),
                env=env,
                stdout=byparr_logfile,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=_preexec if sys.platform != "win32" else None,
            )
            try:
                byparr_logfile.close()
            except Exception as e:
                logger.debug(f"[SteamDB] Could not close byparr logfile handle in parent: {e}")
            cls._managed = True

            # Wait up to 10 seconds for service readiness
            for _ in range(20):
                time.sleep(0.5)
                if cls.is_running():
                    logger.info("[SteamDB] Byparr helper process is now running and responsive.")
                    return True

            logger.warning("[SteamDB] Byparr process launched but socket connect timed out.")
            return False
        except Exception as e:
            logger.error(f"[SteamDB] Failed to launch Byparr process: {e}")
            return False

    @classmethod
    def stop(cls):
        if cls._process and cls._managed:
            proc = cls._process
            cls._process = None
            cls._managed = False
            logger.info("[SteamDB] Terminating Byparr helper process tree...")
            try:
                if sys.platform != "win32":
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                logger.info("[SteamDB] Byparr helper process successfully stopped.")
            except Exception as e:
                logger.debug(f"[SteamDB] Error stopping Byparr process: {e}")

        byparr_dir = cls.find_byparr_dir()
        if byparr_dir and cls.is_running():
            try:
                subprocess.run(["pkill", "-f", str(byparr_dir)], capture_output=True, timeout=2)
            except Exception as _silent_err:
                logger.debug("[steamdb_scraper] stop: ignored %s", _silent_err, exc_info=False)

    @classmethod
    def build_headers(cls, auth_header: Optional[str] = None) -> Dict[str, str]:
        auth = auth_header if auth_header is not None else cls.get_auth_header()
        headers = {"Content-Type": "application/json"}
        if auth:
            if ":" in auth:
                k, v = auth.split(":", 1)
                headers[k.strip()] = v.strip()
            elif auth.lower().startswith("bearer "):
                headers["Authorization"] = auth.strip()
            else:
                headers["Authorization"] = f"Bearer {auth.strip()}"
        return headers

    @classmethod
    def test_connection(cls, service_url: str, auth_header: str = "", timeout_seconds: int = 15) -> Tuple[bool, str, float]:
        url = (service_url or "").strip().rstrip("/")
        if not url:
            return False, "Service URL cannot be empty.", 0.0
        if not (url.startswith("http://") or url.startswith("https://")):
            url = f"http://{url}"

        endpoint = f"{url}/v1"
        headers = cls.build_headers(auth_header)
        payload = {
            "cmd": "request.get",
            "url": "https://steamdb.info/",
            "maxTimeout": timeout_seconds * 1000,
            "returnOnlyCookies": True,
        }

        t0 = time.time()
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 5)
            elapsed = time.time() - t0
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    return False, "Server replied 200 OK, but response was not valid JSON.", elapsed

                if data.get("status") == "ok":
                    sol = data.get("solution", {})
                    sol_status = sol.get("status")
                    if sol_status == 200:
                        return True, f"Verified! SteamDB Turnstile solved successfully in {elapsed:.2f}s.", elapsed
                    return False, f"Solver answered but SteamDB returned status {sol_status}.", elapsed
                return False, f"Solver error: {data.get('message', 'Unknown solver status')}", elapsed
            return False, f"Server returned HTTP {r.status_code}: {r.text[:120]}", elapsed
        except requests.exceptions.Timeout:
            return False, f"Connection timed out after {timeout_seconds} seconds.", time.time() - t0
        except requests.exceptions.ConnectionError as ce:
            return False, f"Failed to connect to {url}: {ce}", time.time() - t0
        except Exception as exc:
            return False, f"Request failed: {exc}", time.time() - t0

    @classmethod
    def verify_steamdb_bypass(cls, timeout_seconds: int = 30) -> bool:
        if not cls.ensure_running():
            return False

        endpoint = f"{cls.get_service_url()}/v1"
        payload = {
            "cmd": "request.get",
            "url": "https://steamdb.info/",
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
            "returnOnlyCookies": True,
        }
        headers = cls.build_headers()
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 10)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    sol = data.get("solution", {})
                    if sol.get("status") == 200:
                        cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                        ua = sol.get("userAgent", "")
                        cf_val = cookies.get("cf_clearance")
                        if cf_val:
                            s = get_settings()
                            s.setValue("steamdb/user_agent", ua)
                            s.setValue("steamdb/cf_clearance", cf_val)
                            s.setValue("steamdb/clearance_timestamp", int(time.time()))
                        logger.info("[SteamDB] Cloudflare Turnstile bypass confirmed working via Byparr.")
                        return True
            logger.warning(f"[SteamDB] SteamDB verification failed (status {r.status_code}): {r.text[:150]}")
        except Exception as e:
            logger.debug(f"[SteamDB] Failed to verify SteamDB bypass via Byparr: {e}")
        return False


# Register clean process termination on normal or unexpected Python exit
atexit.register(ByparrManager.stop)

# ─────────────────────────────────────────────────────────────────────────────
# Request governor: every SteamDB request (fast path and Byparr) goes through
# here so the app as a whole stays polite and never hammers SteamDB from one IP.
# ─────────────────────────────────────────────────────────────────────────────
import threading as _threading


class SteamDBRateLimited(RuntimeError):
    """Raised (or reported) while SteamDB asked us to back off."""


class _SteamDBGovernor:
    MIN_INTERVAL = 2.0          # seconds between SteamDB requests, app-wide
    CACHE_TTL = 600             # identical URL within 10 min is served from memory
    COOLDOWN_BASE = 300         # first rate-limit hit: 5 min
    COOLDOWN_MAX = 3 * 3600     # cap: 3 h
    STRIKE_RESET = 6 * 3600     # strikes decay after 6 h without a hit

    _lock = _threading.Lock()
    _last_request = 0.0
    _cache: Dict[str, Tuple[float, str]] = {}

    @classmethod
    def _settings(cls):
        try:
            return get_settings()
        except Exception as _silent_err:
            logger.debug("[steamdb_scraper] _settings: ignored %s", _silent_err, exc_info=False)
            return None

    @classmethod
    def cooldown_remaining(cls) -> float:
        s = cls._settings()
        until = float(s.value("steamdb/cooldown_until", 0, type=float) or 0) if s else 0.0
        return max(0.0, until - time.time())

    @classmethod
    def note_rate_limited(cls, reason: str = "") -> float:
        s = cls._settings()
        now = time.time()
        strikes = int(s.value("steamdb/strikes", 0, type=int) or 0) if s else 0
        last_hit = float(s.value("steamdb/last_hit", 0, type=float) or 0) if s else 0.0
        if now - last_hit > cls.STRIKE_RESET:
            strikes = 0
        delay = min(cls.COOLDOWN_BASE * (2 ** strikes), cls.COOLDOWN_MAX)
        if s:
            s.setValue("steamdb/cooldown_until", now + delay)
            s.setValue("steamdb/strikes", strikes + 1)
            s.setValue("steamdb/last_hit", now)
        logger.warning(f"[SteamDB] Rate limited ({reason or 'HTTP 429'}); pausing SteamDB requests for {int(delay // 60)} min.")
        return delay

    @classmethod
    def acquire(cls, url: str) -> None:
        """Block until this request may go out; raise if SteamDB asked us to back off."""
        remaining = cls.cooldown_remaining()
        if remaining > 0:
            raise SteamDBRateLimited(f"SteamDB rate limit: retry in {int(remaining // 60) + 1} min")
        with cls._lock:
            wait = cls.MIN_INTERVAL - (time.time() - cls._last_request)
            if wait > 0:
                time.sleep(wait)
            cls._last_request = time.time()

    @classmethod
    def cached(cls, key: str) -> Optional[str]:
        hit = cls._cache.get(key)
        if hit and (time.time() - hit[0]) < cls.CACHE_TTL:
            return hit[1]
        return None

    @classmethod
    def store(cls, key: str, html: str) -> None:
        if html:
            cls._cache[key] = (time.time(), html)
            if len(cls._cache) > 200:
                for k in sorted(cls._cache, key=lambda k: cls._cache[k][0])[:50]:
                    cls._cache.pop(k, None)


def steamdb_cooldown_remaining() -> float:
    """Seconds left on a SteamDB back-off (0 when requests are allowed)."""
    return _SteamDBGovernor.cooldown_remaining()


_RATE_LIMIT_MARKERS = ("too many requests", "rate limited", "you are being rate limited", "error 1015")


def _looks_rate_limited(status: int, text: str) -> bool:
    if status == 429:
        return True
    low = (text or "")[:4000].lower()
    return any(m in low for m in _RATE_LIMIT_MARKERS)


# Public alias for connection test
test_byparr_connection = ByparrManager.test_connection


def verify_steamdb_status(timeout_seconds: int = 5) -> Tuple[bool, str]:
    """
    Checks SteamDB accessibility.
    First tests direct Fast-Path racing (<1s). If fast-path passes, returns (True, "Fast-Path (Direct TLS)").
    If fast-path fails and Byparr is configured, verifies Byparr.
    Returns (True/False, provider_name).
    """
    if cffi_requests:
        try:
            scraper = SteamDBScraper()
            html = scraper.fetch_via_fast_race(f"{STEAMDB_BASE_URL}/", timeout_seconds=min(3.5, float(timeout_seconds)))
            if html and len(html) > 500:
                logger.info("[SteamDB] SteamDB status verified active via Fast-Path.")
                return True, "Fast-Path (Direct TLS)"
        except Exception as e:
            logger.debug(f"[SteamDB] Fast-path status check error: {e}")

    if ByparrManager.is_available():
        ok = ByparrManager.verify_steamdb_bypass(timeout_seconds=timeout_seconds)
        if ok:
            return True, "Byparr"

    return False, "Offline"


# ─────────────────────────────────────────────────────────────────────────────
# SteamDB Scraper class: scrapes patchnotes and depot manifest IDs.
# ─────────────────────────────────────────────────────────────────────────────
class SteamDBScraper:
    """Scrapes SteamDB for patchnotes and depot manifest IDs.
    
    Also provides data endpoints for:
    - Latest build information
    - Depot information (base game, all depots)
    - Folder structure
    - Game image/thumbnails
    - AppID/name wise search
    """

    def __init__(self, solver_url: Optional[str] = None):
        self.solver_url = solver_url or ByparrManager.get_service_url()

    def fetch_via_fast_race(self, url: str, timeout_seconds: float = 4.0, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """
        Direct TLS fetch with browser impersonation (curl_cffi).
        Polite by design: the profile that last worked is tried first and, only
        if it fails, at most two others one after another (no parallel fan-out,
        which multiplied every request by the number of profiles). A 429 or a
        rate-limit page stops immediately and starts the global back-off.
        """
        if not cffi_requests:
            return ""

        s = get_settings()
        sticky = str(s.value("steamdb/fast_profile", "", type=str) or "") if s else ""
        profiles = sorted(FAST_RACE_PROFILES, key=lambda p: 0 if p["name"] == sticky else 1)[:3]

        for profile in profiles:
            try:
                _SteamDBGovernor.acquire(url)
            except SteamDBRateLimited as e:
                logger.info(f"[SteamDB] {e}; skipping {url}")
                return ""
            try:
                headers = profile["headers"].copy()
                if extra_headers:
                    headers.update(extra_headers)
                r = cffi_requests.get(url, impersonate=profile["impersonate"], headers=headers, timeout=timeout_seconds)
            except Exception as e:
                logger.debug(f"[SteamDB] Fast path via '{profile['name']}' failed for {url}: {e}")
                continue
            if _looks_rate_limited(r.status_code, r.text):
                _SteamDBGovernor.note_rate_limited(f"HTTP {r.status_code}")
                return ""
            cf_mitigated = r.headers.get("Cf-Mitigated", "").lower()
            is_challenge = cf_mitigated == "challenge" or "challenges.cloudflare.com" in r.text
            if r.status_code == 200 and not is_challenge and len(r.text) > 500:
                if s and profile["name"] != sticky:
                    s.setValue("steamdb/fast_profile", profile["name"])
                logger.info(f"[SteamDB] Fast path via '{profile['name']}' for {url}")
                return r.text
            if is_challenge:
                logger.info(f"[SteamDB] Cloudflare challenge on fast path for {url}")
                return ""
        return ""

    def fetch_via_byparr(self, url: str, timeout_seconds: int = 60) -> str:
        """
        Fetches the fully-rendered DOM from SteamDB via Byparr (which executes JS & bypasses Turnstile).
        """
        try:
            _SteamDBGovernor.acquire(url)
        except SteamDBRateLimited as e:
            logger.info(f"[SteamDB] {e}; skipping Byparr fetch of {url}")
            return ""

        if not ByparrManager.is_running():
            ByparrManager.ensure_running()

        endpoint = f"{self.solver_url}/v1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
        }
        headers = ByparrManager.build_headers()

        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 15)
            if r.status_code == 200:
                data = r.json()
                sol = data.get("solution", {})
                html = sol.get("response", "")
                if _looks_rate_limited(int(sol.get("status") or 200), html):
                    _SteamDBGovernor.note_rate_limited("Byparr page")
                    return ""

                # Cache cookies & user-agent for fast static requests
                cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                ua = sol.get("userAgent", "")
                cf_val = cookies.get("cf_clearance")
                if cf_val:
                    s = get_settings()
                    s.setValue("steamdb/user_agent", ua)
                    s.setValue("steamdb/cf_clearance", cf_val)
                    s.setValue("steamdb/clearance_timestamp", int(time.time()))

                return html
            else:
                logger.error(f"[SteamDB] Byparr returned status {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[SteamDB] Failed to fetch {url} via Byparr: {e}")

        return ""

    def fetch_rendered_html(self, url: str, timeout_seconds: int = 60, allow_fast_path: bool = True, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """
        Fetches SteamDB HTML. First races multiple direct TLS profiles (fast-path).
        If fast-path is challenged or unavailable and Byparr is configured, falls back to Byparr.
        """
        if get_settings().value("disable_steamdb_scraping", False, type=bool):
            logger.info("[SteamDB] Scraping disabled in Testing settings; skipping fetch.")
            return ""

        cache_key = f"{url}|{sorted((extra_headers or {}).items())}"
        cached = _SteamDBGovernor.cached(cache_key)
        if cached is not None:
            return cached
        if _SteamDBGovernor.cooldown_remaining() > 0:
            return ""

        html = self._fetch_rendered_uncached(url, timeout_seconds, allow_fast_path, extra_headers)
        _SteamDBGovernor.store(cache_key, html)
        return html

    def _fetch_rendered_uncached(self, url: str, timeout_seconds: int, allow_fast_path: bool, extra_headers: Optional[Dict[str, str]]) -> str:
        # 1. Fast-path multi-profile race
        if allow_fast_path and cffi_requests:
            has_byparr = ByparrManager.is_available()
            fast_timeout = 4.0 if has_byparr else 8.0
            t0 = time.time()
            html = self.fetch_via_fast_race(url, timeout_seconds=fast_timeout, extra_headers=extra_headers)
            if html:
                return html
            elapsed = time.time() - t0
            logger.info(f"[SteamDB] Fast-path did not resolve cleanly ({elapsed:.2f}s). Byparr available: {has_byparr}")

        # 2. Byparr fallback (if available)
        if ByparrManager.is_available():
            return self.fetch_via_byparr(url, timeout_seconds=timeout_seconds)

        return ""

    def _parse_patchnotes_html(self, html: str, limit: int = 40) -> List[Dict[str, Any]]:
        """Parses builds from either dynamic client-side JS table or static HTML branches table."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Parser 1: Find the table containing "Patch Title" (rendered by Byparr / browser JS)
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if any("patch title" in h.lower() for h in headers):
                for row in table.find_all("tr")[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    if len(cols) >= 4:
                        date_str = cols[0]
                        day_str = cols[1] if len(cols) > 1 else ""
                        time_str = cols[2] if len(cols) > 2 else ""
                        title_str = cols[3] if len(cols) > 3 else ""
                        build_id = cols[-1] if len(cols) > 4 else ""

                        clean_bid = "".join(re.findall(r"\d+", build_id))
                        if not clean_bid:
                            for a in row.find_all("a", href=True):
                                m = re.search(r"/patchnotes/(\d+)", a["href"])
                                if m:
                                    clean_bid = m.group(1)
                                    break

                        patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{clean_bid}/" if clean_bid else ""
                        for a in row.find_all("a", href=True):
                            if "/patchnotes/" in a["href"]:
                                patch_url = f"{STEAMDB_BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                                break

                        if clean_bid:
                            results.append({
                                "date": date_str,
                                "day": day_str,
                                "time": time_str,
                                "title": title_str or f"Update (Build {clean_bid})",
                                "buildid": clean_bid,
                                "patchnotes_url": patch_url,
                            })
                            if len(results) >= limit:
                                break
                if results:
                    return results

        # Parser 2: Parse static branches/builds table (static HTML from fast-path)
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "build id" in headers or any("build" in h for h in headers):
                for row in table.find_all("tr")[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    clean_bid = ""
                    patch_url = ""
                    for a in row.find_all("a", href=True):
                        m = re.search(r"/patchnotes/(\d+)", a["href"])
                        if m:
                            clean_bid = m.group(1)
                            patch_url = f"{STEAMDB_BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                            break
                    if not clean_bid and cols:
                        for col in cols:
                            digits = "".join(re.findall(r"^\d{7,10}$", col))
                            if digits:
                                clean_bid = digits
                                patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{clean_bid}/"
                                break
                    if clean_bid:
                        branch_name = cols[0] if cols else ""
                        desc = cols[1] if len(cols) > 1 and cols[1] != branch_name else ""
                        title = f"{branch_name} - {desc}".strip(" -") if desc else (branch_name or f"Build {clean_bid}")
                        time_str = cols[-1] if len(cols) > 3 else ""
                        results.append({
                            "date": time_str.split("–")[0].strip() if "–" in time_str else time_str,
                            "day": "",
                            "time": time_str.split("–")[1].strip() if "–" in time_str else "",
                            "title": title or f"Update (Build {clean_bid})",
                            "buildid": clean_bid,
                            "patchnotes_url": patch_url,
                        })
                        if len(results) >= limit:
                            break
                if results:
                    return results

        return results

    def _parse_patchnotes_rss(self, xml_text: str, limit: int = 40) -> List[Dict[str, Any]]:
        """
        Parses official SteamDB XML RSS feed (https://steamdb.info/api/PatchnotesRSS/?appid=<appid>).
        Returns list of patch dictionaries:
        [{date, day, time, title, buildid, patchnotes_url}]
        """
        if not xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            logger.debug(f"[SteamDB] Failed to parse RSS XML: {e}")
            return []

        results = []
        for item in root.findall(".//item"):
            guid = (item.findtext("guid") or "").strip()
            # guid is formatted like 'build#12345678'
            m = re.search(r"\d+", guid)
            build_id = m.group(0) if m else ""

            link = (item.findtext("link") or "").strip()
            if not build_id and link:
                m_link = re.search(r"/patchnotes/(\d+)", link)
                if m_link:
                    build_id = m_link.group(1)

            if not build_id:
                continue

            title = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            date_str = ""
            day_str = ""
            time_str = ""
            if pub_date:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date)
                    date_str = dt.strftime("%d %B %Y")
                    day_str = dt.strftime("%a")
                    time_str = dt.strftime("%H:%M")
                except Exception:
                    date_str = pub_date

            patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{build_id}/"

            results.append({
                "date": date_str,
                "day": day_str,
                "time": time_str,
                "title": title or f"Update (Build {build_id})",
                "buildid": build_id,
                "patchnotes_url": patch_url,
            })
            if len(results) >= limit:
                break

        return results

    def get_patchnotes(self, appid: int, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Scrapes patchnotes for an app.
        1. Fast-Path AJAX section (RenderAppSection): queries SteamDB's pre-rendered section endpoint
           (https://steamdb.info/api/RenderAppSection/?section=patchnotes&appid=<appid>) via Direct TLS
           with X-Requested-With: XMLHttpRequest. Returns up to 50 builds with patch titles in ~200ms.
        2. Fast-Path XML RSS feed (PatchnotesRSS): fallback providing the 10 most recent release builds.
        3. Fast-Path static HTML page: fallback parsing the branches table.
        4. Byparr fallback: headless browser rendering if Byparr is configured and fast-path yielded no builds.
        """
        # 1. Try RenderAppSection endpoint first (provides up to 50 rich builds with patch titles)
        render_url = f"{STEAMDB_BASE_URL}/api/RenderAppSection/?section=patchnotes&appid={appid}"
        ajax_headers = {
            "Accept": "text/html",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{STEAMDB_BASE_URL}/app/{appid}/patchnotes/",
        }
        ajax_html = self.fetch_rendered_html(render_url, extra_headers=ajax_headers)
        if ajax_html:
            results = self._parse_patchnotes_html(ajax_html, limit=limit)
            if results:
                for r in results:
                    r["_source"] = "render_section"
                logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries from RenderAppSection for App {appid}")
                return results

        if _SteamDBGovernor.cooldown_remaining() > 0:
            raise SteamDBRateLimited(f"SteamDB rate limit: retry in {int(_SteamDBGovernor.cooldown_remaining() // 60) + 1} min")

        # 2. Try official XML RSS feed fallback (provides the 10 most recent release builds)
        rss_url = f"{STEAMDB_BASE_URL}/api/PatchnotesRSS/?appid={appid}"
        xml_data = self.fetch_rendered_html(rss_url)
        results = self._parse_patchnotes_rss(xml_data, limit=limit)
        if results:
            for r in results:
                r["_source"] = "rss"
            logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries from RSS for App {appid}")
            return results

        # 3. Fall back to static HTML page (branches table)
        url = f"{STEAMDB_BASE_URL}/app/{appid}/patchnotes/"
        if _SteamDBGovernor.cooldown_remaining() > 0:
            return []
        html = self.fetch_rendered_html(url)
        if html:
            results = self._parse_patchnotes_html(html, limit=limit)
            if results:
                for r in results:
                    r["_source"] = "static_html"

        # 4. Fall back to Byparr (if fast-path yielded no builds and Byparr is available)
        if not results and ByparrManager.is_available():
            logger.info(f"[SteamDB] Fast-path did not yield patchnotes for App {appid}. Falling back to Byparr...")
            byparr_html = self.fetch_via_byparr(url, timeout_seconds=60)
            if byparr_html:
                results = self._parse_patchnotes_html(byparr_html, limit=limit)
                for r in results:
                    r["_source"] = "byparr"

        logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries for App {appid}")
        return results

    def get_patch_depots(self, buildid_or_url: str) -> Dict[str, Dict[str, str]]:
        """
        Scrapes https://steamdb.info/patchnotes/<buildid>/
        Returns dictionary mapping depot_id -> {depot_id, manifest_id, old_manifest_id}
        """
        if str(buildid_or_url).startswith("http"):
            url = str(buildid_or_url)
        else:
            url = f"{STEAMDB_BASE_URL}/patchnotes/{str(buildid_or_url).strip('/')}/"

        html = self.fetch_rendered_html(url)
        if not html:
            return {}

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        # Look for depot headers and history links
        for heading in soup.select(".panel-heading"):
            depot_link = heading.find("a", href=lambda h: h and "/depot/" in h)
            if depot_link:
                d_match = re.search(r"/depot/(\d+)", depot_link["href"])
                if d_match:
                    depot_id = d_match.group(1)
                    
                    # Target manifest ID is in changeid=M:<id> on the depot link
                    m_target = re.search(r"changeid=M:(\d+)", depot_link["href"])
                    manifest_id = m_target.group(1) if m_target else ""
                    
                    old_manifest_id = ""
                    panel = heading.find_parent("div")
                    if panel:
                        manifest_links = [
                            re.search(r"changeid=M:(\d+)", a["href"]).group(1)
                            for a in panel.find_all("a", href=True)
                            if "/history/?changeid=M:" in a["href"] and re.search(r"changeid=M:(\d+)", a["href"])
                        ]
                        # If there are two manifest IDs, the other one is old
                        for mid in manifest_links:
                            if mid != manifest_id:
                                old_manifest_id = mid
                                break

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "manifest_id": manifest_id,
                        "old_manifest_id": old_manifest_id,
                    }

        logger.info(f"[SteamDB] Extracted {len(depots)} depots for {url}")
        return depots

    def get_app_depots(self, appid: str | int) -> Dict[str, Dict[str, Any]]:
        """
        Scrapes https://steamdb.info/app/<appid>/depots/
        Extracts all depots (including DLC depots) with their names, configurations, and sizes.
        Includes OS/Arch detection, size info, and DLC status.
        """
        appid_str = str(appid)
        url = f"{STEAMDB_BASE_URL}/app/{appid_str}/depots/"
        html = self.fetch_rendered_html(url)
        if not html:
            return self._fetch_depots_fallback_steam_store(appid_str)

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        for table in soup.find_all("table"):
            header_row = table.find("tr")
            if not header_row:
                continue
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
            if not ("id" in headers and "configuration" in headers and "size" in headers):
                continue

            for row in table.find_all("tr")[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) >= 3:
                    depot_id = cols[0].get_text(strip=True)
                    if not depot_id.isdigit():
                        continue

                    config_cell = cols[1]
                    size_cell = cols[2]
                    dl_cell = cols[3] if len(cols) > 3 else None

                    # Check for OS badges (may have multiple: Windows, Linux, macOS)
                    os_spans = config_cell.find_all(class_=re.compile(r"depot-os"))
                    detected_oses = []
                    for os_s in os_spans:
                        os_t = os_s.get_text(strip=True).lower()
                        if ("windows" in os_t or "win" in os_t) and "windows" not in detected_oses:
                            detected_oses.append("windows")
                        if "linux" in os_t and "linux" not in detected_oses:
                            detected_oses.append("linux")
                        if ("mac" in os_t or "apple" in os_t) and "macos" not in detected_oses:
                            detected_oses.append("macos")

                    oslist = ",".join(detected_oses) if detected_oses else None

                    # Check for architecture
                    osarch = None
                    text_lower = config_cell.get_text(" ", strip=True).lower()
                    if "64-bit" in text_lower or "x64" in text_lower or "64 bit" in text_lower:
                        osarch = "64"
                    elif "32-bit" in text_lower or "x86" in text_lower or "32 bit" in text_lower:
                        osarch = "32"

                    muted_span = config_cell.find("span", class_=re.compile(r"muted"))
                    if muted_span:
                        name = muted_span.get_text(strip=True)
                    else:
                        raw_name = config_cell.get_text(" ", strip=True)
                        raw_name = re.sub(r"^DLC\s+\d+\s*", "", raw_name).strip()
                        # If raw_name only contains OS and arch tokens, it's not a real depot name!
                        cleaned_words = re.sub(
                            r"\b(windows|linux|macos|64-bit|32-bit|x64|x86|64|32)\b",
                            "",
                            raw_name,
                            flags=re.IGNORECASE,
                        ).strip()
                        if cleaned_words and not re.match(r"^[,\s\-_/]+$", cleaned_words):
                            name = raw_name
                        else:
                            name = None

                    is_dlc = bool(config_cell.find(string=re.compile(r"DLC\s+\d+")) or "dlc" in config_cell.get_text().lower())
                    size_str = size_cell.get_text(strip=True) if size_cell else ""
                    dl_str = dl_cell.get_text(strip=True) if dl_cell else ""
                    size_bytes = self._parse_size_to_bytes(size_str)

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "name": name,
                        "oslist": oslist,
                        "osarch": osarch,
                        "size_str": size_str,
                        "size_bytes": size_bytes,
                        "dl_str": dl_str,
                        "is_dlc": is_dlc,
                    }

        logger.info(f"[SteamDB] Parsed {len(depots)} depots for App {appid_str}")
        return depots

    @staticmethod
    def _parse_size_to_bytes(size_str: str) -> int:
        """Parse a size string like '1.2 GB', '500 MB', '2048 bytes' to bytes."""
        if not size_str:
            return 0
        
        size_str = size_str.strip()
        
        # Handle scientific notation and plain numbers
        match = re.match(r"^([\d+\.]+)\s*([a-zA-Z]+)?$", size_str)
        if not match:
            try:
                return int(float(size_str))
            except ValueError:
                return 0
        
        value = float(match.group(1))
        unit = (match.group(2) or "").lower()
        
        multipliers = {
            "b": 1,
            "byte": 1,
            "bytes": 1,
            "kb": 1024,
            "kib": 1024,
            "mb": 1024**2,
            "mib": 1024**2,
            "gb": 1024**3,
            "gib": 1024**3,
            "tb": 1024**4,
            "tib": 1024**4,
        }
        
        return int(value * multipliers.get(unit, 1))

    def get_latest_build(self, appid: int) -> Optional[Dict[str, Any]]:
        """
        Get the latest/ latest live build for an app from patchnotes.
        Returns dict with buildid, date, title, or None if not available.
        """
        patchnotes = self.get_patchnotes(appid, limit=1)
        if patchnotes:
            latest = patchnotes[0]
            return {
                "buildid": latest.get("buildid"),
                "date": latest.get("date"),
                "title": latest.get("title"),
                "url": latest.get("patchnotes_url"),
            }
        return None

    def get_base_game_depot(self, appid: int) -> Optional[Dict[str, Any]]:
        """
        Get the base game depot information for an app.
        The base game depot is typically the first non-DLC depot with the main app.
        """
        depots = self.get_app_depots(appid)
        if not depots:
            return None
        
        # Find the depot that matches the app (usually depot_id // 10 * 10 ≈ appid, or the first non-DLC depot)
        for depot_id, depot_info in depots.items():
            if not depot_info.get("is_dlc"):
                # This is likely the base game depot
                return {
                    "depot_id": depot_id,
                    "name": depot_info.get("name") or f"Base Depot {depot_id}",
                    "oslist": depot_info.get("oslist"),
                    "osarch": depot_info.get("osarch"),
                    "size_str": depot_info.get("size_str"),
                    "size_bytes": depot_info.get("size_bytes"),
                }
        
        # Fallback: return the first depot
        first_depot_id = list(depots.keys())[0] if depots else None
        if first_depot_id:
            info = depots[first_depot_id]
            return {
                "depot_id": first_depot_id,
                "name": info.get("name") or f"Depot {first_depot_id}",
                "oslist": info.get("oslist"),
                "osarch": info.get("osarch"),
                "size_str": info.get("size_str"),
                "size_bytes": info.get("size_bytes"),
            }
        
        return None

    def get_folder_structure(self, appid: int, depot_id: str) -> Dict[str, Any]:
        """
        Get the folder structure for a specific depot.
        This uses the ACF/local data to determine the expected folder structure.
        """
        from core.steam_helpers import find_steam_install, get_steam_libraries
        
        steam_path = find_steam_install()
        if not steam_path:
            return {"error": "Steam installation not found"}
        
        # Look for the app in steamapps
        app_found = False
        folder_structure = {}
        
        libraries = get_steam_libraries()
        for lib in libraries:
            acf_path = Path(lib) / "steamapps" / f"appappid_{appid}.acf"
            if acf_path.exists():
                app_found = True
                try:
                    content = acf_path.read_text(encoding="utf-8", errors="ignore")
                    # Extract the folder name from the ACF file
                    folder_match = re.search(r'"appfolder"\s*"([^"]+)"', content)
                    folder_name = folder_match.group(1) if folder_match else None
                    
                    # Extract cutting room if present
                    cut_match = re.search(r'"allcutscenes"\s*"([^"]*)"', content)
                    
                    folder_structure = {
                        "appfolder": folder_name,
                        "library": str(lib),
                        "acf_path": str(acf_path),
                        "app_found": True,
                    }
                except Exception as e:
                    logger.debug(f"Error reading ACF for app {appid}: {e}")
                break
        
        if not app_found:
            folder_structure = {"error": f"AppID {appid} not found in Steam library"}
        
        return folder_structure

    def get_game_image(self, appid: int) -> Optional[str]:
        """
        Get the game thumbnail/image URL for an AppID.
        Tries SteamDB, Steam Store API, and local assets.
        """
        # Try SteamDB first - the header/image URL
        try:
            # SteamDB often has header images
            image_url = None
            
            # Try Steam Store API for header URL
            import requests as rq
            resp = rq.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}",
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code == 200:
                data = resp.json()
                app_data = data.get(str(appid), {}).get("data", {})
                header_url = app_data.get("header_image")
                if header_url:
                    return header_url
            
            # Try fetching from SteamDB via API
            # The header image can be found at steamdb's app page
            # For now, return None and let the caller use fallback
            pass
            
        except Exception as e:
            logger.debug(f"Error fetching game image for AppID {appid}: {e}")
        
        # Try to get from local database
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            app_info = db.get_app_info(str(appid))
            if app_info and app_info.get("header_image"):
                return app_info["header_image"]
        except Exception:
            pass
        
        return None

    def search_by_appid(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for games by AppID.
        Returns list of matching games with basic info.
        """
        results = []
        
        # Try to parse as a number
        appid_match = re.search(r"(\d+)", query)
        if appid_match:
            appid = appid_match.group(1)
            try:
                # Get app info from database
                from managers.db_manager import DatabaseManager
                db = DatabaseManager()
                app_info = db.get_app_info(appid)
                if app_info:
                    results.append({
                        "appid": appid,
                        "name": app_info.get("name", f"App {appid}"),
                        "type": app_info.get("type", "game"),
                    })
                else:
                    # Try Steam Store API
                    import requests as rq
                    resp = rq.get(
                        f"https://store.steampowered.com/api/appdetails?appids={appid}",
                        timeout=5,
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        app_data = data.get(appid, {}).get("data", {})
                        if app_data.get("name"):
                            results.append({
                                "appid": appid,
                                "name": app_data["name"],
                                "type": app_data.get("type", "game"),
                            })
            except Exception as e:
                logger.debug(f"Search by AppID '{appid}' failed: {e}")
        
        # If no numeric match, or as fallback, try name search
        if not results and not appid_match:
            # Name search - query SteamDB or Store
            try:
                from core.morrenus_api import search_games
                search_results = search_games(query)
                results.extend(search_results)
            except Exception:
                pass
        
        return results

    def search_by_name(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for games by name.
        Returns list of matching games with AppID and basic info.
        """
        results = []
        
        # Try Morrenus API search first
        try:
            from core.morrenus_api import search_games
            search_results = search_games(query)
            results.extend(search_results)
        except Exception as e:
            logger.debug(f"Name search via Morrenus API failed: {e}")
        
        # Fallback: Steam Store API search
        if not results:
            try:
                import requests as rq
                resp = rq.get(
                    "https://store.steampowered.com/search/",
                    params={
                        "term": query,
                        "filter": "global",
                        "cc": "us",
                        "l": "english",
                    },
                    timeout=5,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200:
                    # Parse the search results HTML
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for item in soup.select(".search_result")[:10]:
                        appid_match = item.get("data-appid")
                        name_match = item.select_one(".title")
                        if appid_match and name_match:
                            results.append({
                                "appid": appid_match,
                                "name": name_match.get_text(strip=True),
                            })
            except Exception as e:
                logger.debug(f"Steam Store name search failed: {e}")
        
        return results

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for games by AppID or name.
        Tries AppID search first, then name search.
        """
        # Try AppID search first
        appid_results = self.search_by_appid(query)
        if appid_results:
            return appid_results
        
        # Fallback to name search
        return self.search_by_name(query)

    def get_app_info_with_data(self, appid: int) -> Dict[str, Any]:
        """
        Get comprehensive app information including:
        - Latest build
        - Base game depot
        - Folder structure
        - Game image
        - All depots
        - Patchnotes
        """
        # Gather all data in parallel for efficiency
        futures = {}
        
        # Latest build
        try:
            latest_future = self._get_latest_build(appid)
            futures["latest_build"] = latest_future
        except Exception as e:
            logger.debug(f"Failed to get latest build: {e}")
            futures["latest_build"] = None
        
        # Base game depot
        try:
            depot_future = self.get_base_game_depot(appid)
            futures["base_game_depot"] = depot_future
        except Exception as e:
            logger.debug(f"Failed to get base game depot: {e}")
            futures["base_game_depot"] = None
        
        # Folder structure
        try:
            folder_future = self.get_folder_structure(appid, "")
            futures["folder_structure"] = folder_future
        except Exception as e:
            logger.debug(f"Failed to get folder structure: {e}")
            futures["folder_structure"] = None
        
        # Game image
        try:
            image_future = self.get_game_image(appid)
            futures["game_image"] = image_future
        except Exception as e:
            logger.debug(f"Failed to get game image: {e}")
            futures["game_image"] = None
        
        # All depots
        try:
            depots_future = self.get_app_depots(appid)
            futures["all_depots"] = depots_future
        except Exception as e:
            logger.debug(f"Failed to get all depots: {e}")
            futures["all_depots"] = {}
        
        # Patchnotes
        try:
            patchnotes_future = self.get_patchnotes(appid, limit=5)
            futures["patchnotes"] = patchnotes_future
        except Exception as e:
            logger.debug(f"Failed to get patchnotes: {e}")
            futures["patchnotes"] = []
        
        # Compile results
        result = {
            "appid": str(appid),
        }
        
        for key, future in futures.items():
            try:
                if future is not None:
                    result[key] = future if isinstance(future, dict) else future.get() if hasattr(future, 'get') else future
            except Exception:
                result[key] = None
        
        return result

    def _get_latest_build(self, appid: int) -> Optional[Dict[str, Any]]:
        """Helper to get latest build in a cleaner way."""
        return self.get_latest_build(appid)


# Convenience functions for quick use
def get_latest_build(appid: int) -> Optional[Dict[str, Any]]:
    """Quick access to get the latest build for an app."""
    scraper = SteamDBScraper()
    return scraper.get_latest_build(appid)


def get_base_game_depot(appid: int) -> Optional[Dict[str, Any]]:
    """Quick access to get the base game depot for an app."""
    scraper = SteamDBScraper()
    return scraper.get_base_game_depot(appid)


def get_folder_structure(appid: int, depot_id: str = "") -> Dict[str, Any]:
    """Quick access to get folder structure for a depot."""
    scraper = SteamDBScraper()
    return scraper.get_folder_structure(appid, depot_id)


def get_game_image(appid: int) -> Optional[str]:
    """Quick access to get game thumbnail/image URL."""
    scraper = SteamDBScraper()
    return scraper.get_game_image(appid)


def search_games(query: str) -> List[Dict[str, Any]]:
    """Quick search for games by AppID or name."""
    scraper = SteamDBScraper()
    return scraper.search(query)


def get_app_info_with_data(appid: int) -> Dict[str, Any]:
    """Quick access to get comprehensive app info with all data endpoints."""
    scraper = SteamDBScraper()
    return scraper.get_app_info_with_data(appid)


# SteamDB App Parser integration
def parse_steamdb_dlc(appid: str, html: str) -> Dict[str, any]:
    """
    Parse DLC and related apps from a SteamDB app page HTML.
    Returns structured data compatible with SLSsteam's DlcData/AdditionalApps.
    
    This is the Python equivalent of GreasyFork script 543010-steamdb-app-parser.
    """
    from .steamdb_app_parser import SteamDBAppParser
    
    return SteamDBAppParser.parse_steamdb_page(appid, html)


def generate_dlc_yaml(appid: str, dlc_list: List[Dict[str, str]]) -> str:
    """
    Generate DlcData YAML for a main app and its DLC.
    Compatible with SLSsteam format from the GreasyFork script.
    """
    from .steamdb_app_parser import generate_dlc_yaml as _generate
    return _generate(appid, dlc_list)


def generate_additional_apps_yaml(appid: str, additional_apps: List[Dict[str, str]]) -> str:
    """
    Generate AdditionalApps YAML for a main app and its linked apps.
    Compatible with SLSsteam format from the GreasyFork script.
    """
    from .steamdb_app_parser import generate_additional_apps_yaml as _generate
    return _generate(appid, additional_apps)


# Register clean process termination
atexit.register(ByparrManager.stop)

# ─────────────────────────────────────────────────────────────────────────────
# Restored compatibility API for callers that predate the EXE-fingerprinter
# rework (gamelibrary_v2 uses SteamDBBuildsCache).
# ─────────────────────────────────────────────────────────────────────────────

try:
    import zstandard as zstd
except ImportError:
    zstd = None

import sqlite3
import json
import threading


def parse_size_to_bytes(size_str: str) -> int:
    """Converts human readable size string like '21.64 MiB' or '1.31 GiB' to byte count."""
    if not size_str or "no size" in size_str.lower():
        return 0

    units = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000 * 1000,
        "mib": 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gib": 1024 * 1024 * 1024,
        "tb": 1024 * 1024 * 1024 * 1024,
        "tib": 1024 * 1024 * 1024 * 1024,
    }

    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)", size_str.strip())
    if m:
        try:
            val = float(m.group(1))
            unit = m.group(2).lower()
            return int(val * units.get(unit, 1))
        except Exception as _silent_err:
            logger.debug("[steamdb_scraper] parse_size_to_bytes: ignored %s", _silent_err, exc_info=False)
    return 0


class SteamDBBuildsCache:
    """Manages persistent zstandard-compressed SQLite caching of the last 6 builds in its own database (steamdb_builds.db)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SteamDBBuildsCache, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        with self._lock:
            if self._initialized:
                return

            from utils.helpers import get_data_file_path
            # Migrate legacy root file if present
            legacy_path = get_base_path() / "steamdb_builds.db"
            target_path = get_base_path() / "db" / "steamdb_builds.db"
            if legacy_path.exists() and not target_path.exists():
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.move(str(legacy_path), str(target_path))
                    logger.info(f"[SteamDB Cache] Migrated legacy steamdb_builds.db from root to {target_path}")
                except Exception as e:
                    logger.warning(f"[SteamDB Cache] Failed to migrate legacy steamdb_builds.db: {e}")

            self.db_path = get_data_file_path("steamdb_builds.db")
            self._conn_lock = threading.RLock()
            self.cctx = zstd.ZstdCompressor(level=3) if zstd else None
            self.dctx = zstd.ZstdDecompressor() if zstd else None
            self._init_db()
            self._initialized = True

    def _init_db(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS builds (
                        appid INTEGER PRIMARY KEY,
                        builds_blob BLOB,
                        last_updated INTEGER
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to init database at {self.db_path}: {e}")

    def get_builds(self, appid: int) -> List[Dict[str, Any]]:
        """Retrieves cached builds (up to 50) for appid, decompressed from zstd."""
        return self.get_builds_with_age(appid)[0]

    def get_builds_with_age(self, appid: int) -> tuple:
        """Returns (builds: list, age_seconds: float). age_seconds is -1 if not cached."""
        try:
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT builds_blob, last_updated FROM builds WHERE appid = ?", (int(appid),))
                row = cur.fetchone()
                if row and row[0]:
                    raw_bytes = row[0]
                    last_updated = row[1] or 0
                    if self.dctx:
                        try:
                            decompressed = self.dctx.decompress(raw_bytes)
                        except Exception:
                            decompressed = raw_bytes
                    else:
                        decompressed = raw_bytes
                    builds = json.loads(decompressed.decode("utf-8"))
                    age = time.time() - last_updated
                    return builds, age
        except Exception as e:
            logger.debug(f"[SteamDB Cache] Failed to read cached builds for {appid}: {e}")
        return [], -1

    def save_builds(self, appid: int, builds: List[Dict[str, Any]]):
        """Saves up to 50 most recent builds into steamdb_builds.db with zstandard compression."""
        if not builds:
            return
        top_builds = builds[:50]
        try:
            json_bytes = json.dumps(top_builds).encode("utf-8")
            compressed_blob = self.cctx.compress(json_bytes) if self.cctx else json_bytes

            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO builds (appid, builds_blob, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(appid) DO UPDATE SET
                        builds_blob = excluded.builds_blob,
                        last_updated = excluded.last_updated
                """, (int(appid), compressed_blob, int(time.time())))
                conn.commit()
                logger.info(f"[SteamDB Cache] Successfully cached {len(top_builds)} builds for App {appid} in {self.db_path.name}")
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to save builds for {appid}: {e}")

    def update_build_depots(self, appid: int, buildid: str, depots: Dict[str, Any]):
        """Updates depot information for a cached build entry."""
        builds = self.get_builds(appid)
        updated = False
        for b in builds:
            if str(b.get("buildid")) == str(buildid):
                b["depots"] = depots
                updated = True
                break
        if updated:
            self.save_builds(appid, builds)

    def get_build_depots(self, buildid: str):
        """Returns depots dict for a cached build (reads steamdb_builds.db directly)."""
        try:
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT appid, builds_blob FROM builds")
                for row in cur.fetchall():
                    raw_bytes = row[1]
                    if self.dctx:
                        try:
                            decompressed = self.dctx.decompress(raw_bytes)
                        except Exception:
                            decompressed = raw_bytes
                    else:
                        decompressed = raw_bytes
                    builds = json.loads(decompressed.decode("utf-8"))
                    for b in builds:
                        if str(b.get("buildid")) == str(buildid):
                            return b.get("depots") or {}
        except Exception as e:
            logger.debug(f"[SteamDB Cache] Failed to look up build depots for {buildid}: {e}")
        return {}
