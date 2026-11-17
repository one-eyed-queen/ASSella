"""Hubcap manifest client (lean).

What the .exe flow uses:

* ``search_games(query)`` — the Hubcap /search source for the unified search
* ``download_manifest(app_id)`` — the cached Hubcap bundle (depot keys + public
  manifest GIDs) the DepotDownloader "-manifest-only" fingerprint needs

The old generation-by-MRC engine (SpaceBunny / wudrm) and the library/update
helpers (stats, usage, branch bundles, depot-key indices) were removed; the
fingerprint path never calls them.
"""
from __future__ import annotations

import datetime  # noqa: F401  (kept for log formatting compatibility)
import logging
import os
import ssl
import tempfile
import threading
import time  # noqa: F401
from pathlib import Path
from typing import Optional, Dict, List, Union, Tuple, Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://hubcapmanifest.com/api/v1"
DEFAULT_SEARCH_LIMIT = 100
MAX_SEARCH_LIMIT = 100

API_ERROR_MESSAGES = {
    400: "Bad Request. The request parameters were invalid.",
    401: "Invalid or missing API key. Please check your credentials in Settings.",
    403: "Access denied. Your account may be blocked or the App ID is not accessible.",
    404: "Manifest not found on Hubcap. The App ID may be incorrect, unavailable on the server, or belongs to a DLC/depot rather than the base game.",
    429: "Daily API limit exceeded. Please try again later.",
    500: "Server error. The manifest may be corrupted or temporarily unavailable on Hubcap.",
    502: "Bad Gateway. Hubcap server is temporarily unreachable.",
    503: "Service Unavailable. Hubcap server is temporarily offline or undergoing maintenance.",
    504: "Gateway Timeout. Hubcap server took too long to respond.",
}


class SSLAdapter(HTTPAdapter):
    """TLS-unverified adapter used ONLY when the user explicitly opted out."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_thread_local = threading.local()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def tls_verification_enabled() -> bool:
    """True when Hubcap TLS certificates are verified (the default)."""
    try:
        return not get_settings().value("hubcap_disable_tls_verify", False, type=bool)
    except Exception:
        return True


def get_session() -> requests.Session:
    """Thread-local session; TLS verified unless the user opted out."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        if tls_verification_enabled():
            session.verify = True
        else:
            logger.warning(
                "[HubcapAPI] TLS certificate verification is DISABLED by user setting "
                "(hubcap_disable_tls_verify). API keys and depot keys are exposed to "
                "interception on this connection."
            )
            session.verify = False
            session.mount("https://", SSLAdapter())
        _thread_local.session = session
    return _thread_local.session


def _get_headers() -> Optional[Dict[str, str]]:
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)
    if not api_key:
        logger.warning("Hubcap API key is not set in settings.")
        return None
    return {"Authorization": f"Bearer {api_key}"}


def _handle_request_exception(e: Exception, context: str) -> str:
    error_str = str(e).lower()
    if "429" in error_str:
        logger.warning(f"{context} rate limited (HTTP 429). Backing off.")
        return API_ERROR_MESSAGES.get(429, "Rate limit reached. Please wait before retrying.")

    logger.error(f"{context} failed: {e}")

    if isinstance(e, requests.exceptions.HTTPError):
        response = getattr(e, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            for code in (404, 401, 403, 400, 429, 500, 502, 503, 504):
                if str(code) in error_str:
                    status_code = code
                    break

        if isinstance(status_code, int) and status_code in API_ERROR_MESSAGES:
            return API_ERROR_MESSAGES[status_code]

        try:
            if response is not None and "application/json" in response.headers.get("content-type", ""):
                error_detail = response.json().get("detail")
                if error_detail:
                    return f"API Error ({status_code or 'Unknown'}): {error_detail}"
        except Exception:
            pass

        if status_code and status_code != "N/A":
            return f"API Error ({status_code})"
        return f"HTTP Request Failed: {e}"

    if "404" in error_str or "not found" in error_str:
        return API_ERROR_MESSAGES[404]

    if "ssl" in error_str or "wrong_version_number" in error_str:
        return "SSL connection failed. Check proxy/firewall settings."

    if "expecting value" in error_str or "line 1 column 1" in error_str or "blocked" in error_str:
        return "Gateway returned HTML or invalid payload (ISP block detected. Try the built-in bypass or a proxy)."

    return f"Request Failed: {e}"


def _make_json_request(
    method: str, endpoint: str, params: dict = None
) -> Union[Dict, List]:
    headers = _get_headers()
    if not headers:
        return {"error": "API Key is not set. Please set it in Settings."}

    url = f"{BASE_URL}{endpoint}"

    try:
        from utils.isp_bypass import execute_hubcap_request

        response = execute_hubcap_request(
            get_session(), method, url, headers=headers, params=params, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        error_msg = _handle_request_exception(e, f"API {method} to {endpoint}")
        return {"error": error_msg}


def search_games(
    query: str, limit: int = DEFAULT_SEARCH_LIMIT
) -> Dict:
    """Search Hubcap for games by name or AppID using the free /search endpoint."""
    logger.info(f"Searching Hubcap API for: {query}")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = DEFAULT_SEARCH_LIMIT
    normalized_limit = max(1, min(normalized_limit, MAX_SEARCH_LIMIT))

    # ── Path 1: numeric query — exact AppID match first ─────────────────
    if query.strip().isdigit():
        data = _make_json_request(
            "GET", "/search",
            params={"q": query.strip(), "appid": "true", "limit": normalized_limit},
        )
        if isinstance(data, dict) and "error" not in data:
            results = data.get("results") or []
            total = data.get("total_matches", len(results))
            if results:
                logger.info(f"[search_games] Exact AppID match for '{query}': {len(results)} result(s)")
                return {"results": results, "total_count": total}
            logger.info(f"[search_games] AppID '{query}' not found in Hubcap — retrying as name search")

    # ── Path 2: name search ─────────────────────────────────────────────
    data = _make_json_request(
        "GET", "/search",
        params={"q": query.strip(), "limit": normalized_limit},
    )
    if isinstance(data, dict) and "error" not in data:
        results = data.get("results") or []
        total = data.get("total_matches", len(results))
        logger.info(f"[search_games] Name search for '{query}': {len(results)} result(s) (total_matches={total})")
        return {"results": results, "total_count": total}

    # ── Path 3: legacy /library fallback ────────────────────────────────
    logger.warning(f"[search_games] /search failed for '{query}', falling back to /library")
    offset = 0
    all_games: list[Any] = []
    total_count: Optional[int] = None

    while True:
        lib_data = _make_json_request(
            "GET", "/library",
            params={
                "search": query,
                "limit": normalized_limit,
                "offset": offset,
                "sort_by": "name",
            },
        )
        if isinstance(lib_data, dict) and "error" in lib_data:
            return lib_data

        page_games: list[Any] = []
        if isinstance(lib_data, dict):
            if isinstance(lib_data.get("games"), list):
                page_games = lib_data["games"]
            elif isinstance(lib_data.get("results"), list):
                page_games = lib_data["results"]
            if total_count is None:
                try:
                    total_count = int(lib_data.get("total_count", 0))
                except (TypeError, ValueError):
                    total_count = None
        elif isinstance(lib_data, list):
            page_games = lib_data

        if not page_games:
            break

        all_games.extend(page_games)
        offset += len(page_games)

        if total_count is not None and offset >= total_count:
            break
        if len(page_games) < normalized_limit:
            break

    return {"results": all_games, "total_count": total_count or len(all_games)}


def get_manifest_zip_path(app_id, branch: str = "public") -> Path:
    """Local cache path of the Hubcap bundle for an app/branch pair."""
    manifests_dir = Path(get_base_path()) / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    if branch and branch != "public":
        return manifests_dir / f"accela_fetch_{app_id}_branch_{branch}.zip"
    return manifests_dir / f"accela_fetch_{app_id}.zip"


def download_manifest(
    app_id, branch: str = "public", force_update: bool = True
) -> Tuple[Optional[str], Optional[str]]:
    """Fetch the cached Hubcap bundle (public only — depot keys + manifest GIDs).

    The Hubcap endpoint has no branch parameter; it always serves the public
    bundle. Returns ``(filepath, None)`` on success, ``(None, error)`` on failure.
    """
    headers = _get_headers()
    if not headers:
        return None, "API Key is not set. Please set it in Settings."

    branch = branch or "public"

    def _build_url(with_force_update: bool) -> str:
        url = f"{BASE_URL}/manifest/{app_id}"
        if with_force_update:
            url += "?force_update=true"
        return url

    public_path = get_manifest_zip_path(app_id, "public")
    manifests_dir = public_path.parent

    logger.info(f"Downloading public manifest bundle {app_id} to {public_path} (force_update={force_update})")

    from utils.isp_bypass import execute_hubcap_request

    try:
        manifests_dir.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{public_path.name}.", suffix=".part", dir=manifests_dir
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)
    except OSError as e:
        return None, f"Could not prepare manifest cache file: {e}"

    def _stream_to_file(url: str) -> None:
        r = execute_hubcap_request(
            get_session(), "GET", url, headers=headers, stream=True, timeout=60
        )
        r.raise_for_status()
        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    def _cleanup_partial() -> None:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    try:
        _stream_to_file(_build_url(force_update))
    except Exception as primary_err:
        logger.warning(
            f"Download manifest with force_update={force_update} failed for {app_id}: {primary_err}"
        )
        if not force_update:
            _cleanup_partial()
            return None, _handle_request_exception(primary_err, f"Download {app_id}")
        logger.info(f"Retrying download for {app_id} using standard endpoint (force_update=False)...")
        try:
            _stream_to_file(_build_url(False))
        except Exception as fallback_err:
            _cleanup_partial()
            return None, _handle_request_exception(fallback_err, f"Download {app_id} (fallback)")

    try:
        os.replace(temp_path, public_path)
    except OSError as e:
        _cleanup_partial()
        return None, f"Could not replace cached manifest: {e}"

    return str(public_path), None
