"""
EXE-fingerprinter API client.

Talks to the private EXE-fingerprinter API (see the sibling
``exe-fingerprinter-api`` repo). The client:

- computes a stable hardware ID (HWID) for this machine,
- logs in with a username + password stored in settings,
- keeps the issued token in settings until it expires,
- exposes search / depot-key / fingerprint helpers.

Every call is best-effort: on any failure the caller keeps using the local
Hubcap/Steam path, so the API being down never blocks the app.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://exe-fingerprinter.vercel.app"
TIMEOUT = 20.0


# ── HWID ───────────────────────────────────────────────────────────────────

def _machine_id() -> str:
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
        except OSError:
            continue
    return ""


def _primary_mac() -> str:
    try:
        import uuid
        return str(uuid.getnode())
    except Exception:
        return ""


def compute_hwid() -> str:
    """Stable per-machine fingerprint (SHA-256 of stable machine identifiers)."""
    parts = [
        platform.system(),
        platform.machine(),
        _machine_id(),
        _primary_mac(),
        socket.gethostname(),
        os.environ.get("USER", os.environ.get("USERNAME", "")),
    ]
    raw = "|".join(part for part in parts if part)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── result types ───────────────────────────────────────────────────────────

@dataclass
class ApiResult:
    ok: bool
    data: Any = None
    error: str = ""


@dataclass
class ApiConfig:
    base_url: str
    username: str
    password: str
    token: str
    token_expiry: int


def _settings():
    from utils.settings import get_settings
    return get_settings()


def load_config() -> ApiConfig:
    settings = _settings()
    return ApiConfig(
        base_url=(settings.value("exefp_api_base", DEFAULT_API_BASE, type=str) or DEFAULT_API_BASE).rstrip("/"),
        username=settings.value("exefp_api_username", "", type=str) or "",
        password=settings.value("exefp_api_password", "", type=str) or "",
        token=settings.value("exefp_api_token", "", type=str) or "",
        token_expiry=int(settings.value("exefp_api_token_expiry", 0, type=int) or 0),
    )


def save_config(base_url: str = "", username: str = "", password: str = "",
                token: str = "", token_expiry: int = 0) -> None:
    settings = _settings()
    if base_url:
        settings.setValue("exefp_api_base", base_url.rstrip("/"))
    if username:
        settings.setValue("exefp_api_username", username)
    if password:
        settings.setValue("exefp_api_password", password)
    if token is not None:
        settings.setValue("exefp_api_token", token)
    if token_expiry is not None:
        settings.setValue("exefp_api_token_expiry", int(token_expiry))
    settings.sync()


def clear_token() -> None:
    save_config(token="", token_expiry=0)


# ── request plumbing ───────────────────────────────────────────────────────

def _headers(token: str) -> Dict[str, str]:
    headers = {"User-Agent": "EXE-fingerprinter-Client/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(method: str, url: str, token: str = "", timeout: float = TIMEOUT, **kwargs) -> ApiResult:
    try:
        response = requests.request(
            method, url, headers=_headers(token), timeout=timeout, **kwargs
        )
    except requests.RequestException as exc:
        return ApiResult(False, error=f"network error: {exc}")

    if response.status_code == 401:
        clear_token()
        return ApiResult(False, error="token rejected — sign in again")
    if response.status_code == 403:
        return ApiResult(False, error="account is locked to another device")
    if response.status_code >= 400:
        detail = ""
        try:
            detail = (response.json() or {}).get("detail", "")
        except ValueError:
            detail = response.text[:200]
        return ApiResult(False, error=detail or f"HTTP {response.status_code}")

    try:
        return ApiResult(True, data=response.json())
    except ValueError:
        return ApiResult(False, error="API returned invalid JSON")


# ── auth ───────────────────────────────────────────────────────────────────

def login(base_url: Optional[str] = None, username: Optional[str] = None,
          password: Optional[str] = None) -> ApiResult:
    config = load_config()
    base = (base_url or config.base_url).rstrip("/")
    user = username or config.username
    pw = password or config.password
    if not user or not pw:
        return ApiResult(False, error="username and password are required")

    payload = {"username": user, "password": pw, "hwid": compute_hwid()}
    result = _request("POST", f"{base}/api/v1/auth/login", json=payload)
    if not result.ok:
        return result

    data = result.data or {}
    token = data.get("token", "")
    if not token:
        return ApiResult(False, error="API did not return a token")

    save_config(
        base_url=base,
        username=user,
        password=pw,
        token=token,
        token_expiry=int(time.time()) + 30 * 24 * 3600,
    )
    logger.info("[EXEFP API] Signed in as %s", user)
    return ApiResult(True, data=data)


def register(base_url: Optional[str], username: str, password: str) -> ApiResult:
    base = (base_url or load_config().base_url).rstrip("/")
    payload = {"username": username, "password": password, "hwid": compute_hwid()}
    result = _request("POST", f"{base}/api/v1/auth/register", json=payload)
    if not result.ok:
        return result
    data = result.data or {}
    token = data.get("token", "")
    if token:
        save_config(
            base_url=base,
            username=username,
            password=password,
            token=token,
            token_expiry=int(time.time()) + 30 * 24 * 3600,
        )
    return ApiResult(True, data=data)


def current_token() -> str:
    config = load_config()
    if config.token and config.token_expiry and config.token_expiry < int(time.time()):
        clear_token()
        return ""
    return config.token


def ensure_token() -> ApiResult:
    """Return a valid token, signing in again if the stored one expired."""
    token = current_token()
    if token:
        return ApiResult(True, data={"token": token})
    config = load_config()
    if not config.username or not config.password:
        return ApiResult(False, error="not signed in")
    result = login()
    if not result.ok:
        return result
    return ApiResult(True, data={"token": current_token()})


def is_configured() -> bool:
    config = load_config()
    return bool(config.base_url and config.username and config.password)


# ── data calls ─────────────────────────────────────────────────────────────

def search(query: str) -> ApiResult:
    config = load_config()
    auth = ensure_token()
    if not auth.ok:
        return auth
    return _request(
        "GET",
        f"{config.base_url}/api/v1/search",
        token=current_token(),
        params={"q": query},
    )


def app_info(appid: int) -> ApiResult:
    config = load_config()
    auth = ensure_token()
    if not auth.ok:
        return auth
    return _request(
        "GET", f"{config.base_url}/api/v1/app/{int(appid)}", token=current_token()
    )


def depots(appid: int, refresh: bool = False, timeout: float = TIMEOUT) -> ApiResult:
    config = load_config()
    auth = ensure_token()
    if not auth.ok:
        return auth
    params = {"refresh": "true"} if refresh else None
    return _request(
        "GET",
        f"{config.base_url}/api/v1/depots/{int(appid)}",
        token=current_token(),
        timeout=timeout,
        params=params,
    )


def fingerprint(appid: int, refresh: bool = False) -> ApiResult:
    config = load_config()
    auth = ensure_token()
    if not auth.ok:
        return auth
    params = {"refresh": "true"} if refresh else None
    return _request(
        "GET",
        f"{config.base_url}/api/v1/fingerprint/{int(appid)}",
        token=current_token(),
        params=params,
    )


def depot_key_map(appid: int, refresh: bool = False, timeout: float = TIMEOUT) -> Dict[str, Dict[str, str]]:
    """Convenience: ``{depot_id: {"key": ..., "manifest": ...}}`` (empty on failure)."""
    result = depots(appid, refresh=refresh, timeout=timeout)
    if not result.ok or not isinstance(result.data, dict):
        return {}
    mapping: Dict[str, Dict[str, str]] = {}
    for entry in result.data.get("depots", []):
        depot_id = str(entry.get("depot_id") or "")
        if not depot_id:
            continue
        mapping[depot_id] = {
            "key": entry.get("depot_key") or "",
            "manifest": str(entry.get("manifest_id") or ""),
        }
    return mapping


def status() -> Dict[str, Any]:
    """Small status record for the settings UI."""
    config = load_config()
    token = current_token()
    return {
        "configured": bool(config.base_url and config.username and config.password),
        "base_url": config.base_url,
        "username": config.username,
        "has_token": bool(token),
        "token_expiry": config.token_expiry,
        "hwid": compute_hwid(),
    }
