"""Steam lookup helpers for EXE-fingerprinter.

Only the REST lookups the .exe flow needs are kept: the SteamCMD API for
depot catalogues (Windows, DLC skipped) and nothing PICS/Store/SteamDB
shaped — those belong to the library/update tooling that was removed.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_STEAMCMD_API = "https://api.steamcmd.net/v1"
_TIMEOUT = 5


def fetch_steamcmd_info(app_id: str, include_header: bool = True) -> dict[str, Any]:
    """App metadata from the SteamCMD REST API.

    Returns a lean dict with the depot catalogue (Windows content depots,
    DLC skipped), build id, name and install dir. ``{}`` on failure.
    """
    appid_str = str(app_id)
    url = f"{_STEAMCMD_API}/info/{appid_str}"
    max_retries = 2
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=_TIMEOUT)
            if res.status_code == 200:
                payload = res.json()
                if payload.get("status") == "success":
                    app_data = payload.get("data", {}).get(appid_str, {})
                    if not isinstance(app_data, dict) or not app_data:
                        return {}
                    depots_raw = app_data.get("depots", {})
                    if not isinstance(depots_raw, dict):
                        depots_raw = {}
                    branches_raw = depots_raw.get("branches", {})
                    if not isinstance(branches_raw, dict):
                        branches_raw = {}

                    depot_info: dict[str, dict[str, Any]] = {}
                    for depot_id, depot_data in depots_raw.items():
                        if not str(depot_id).isdigit() or not isinstance(depot_data, dict):
                            continue
                        config = depot_data.get("config", {}) or {}
                        manifests = depot_data.get("manifests", {}) or {}
                        manifest_public = manifests.get("public", {}) or {}
                        manifest_id = (
                            manifest_public.get("gid")
                            if isinstance(manifest_public, dict)
                            else manifest_public
                        )
                        depot_size = None
                        if isinstance(manifest_public, dict) and (
                            manifest_public.get("size") or manifest_public.get("download")
                        ):
                            depot_size = manifest_public.get("size") or manifest_public.get("download")
                        elif depot_data.get("maxsize"):
                            depot_size = depot_data["maxsize"]
                        dlc_appid = depot_data.get("dlcappid")
                        depot_info[str(depot_id)] = {
                            "name": depot_data.get("name"),
                            "oslist": config.get("oslist"),
                            "osarch": config.get("osarch") or depot_data.get("osarch"),
                            "language": config.get("language"),
                            "steamdeck": config.get("steamdeck") == "1",
                            "size": depot_size,
                            "manifest_id": manifest_id,
                            "manifests": depot_data.get("manifests"),
                            "dlcappid": str(dlc_appid) if dlc_appid is not None else None,
                            "is_dlc": bool(dlc_appid),
                        }

                    public_branch = branches_raw.get("public", {})
                    build_id = public_branch.get("buildid") if isinstance(public_branch, dict) else None
                    time_updated = (
                        public_branch.get("timeupdated") if isinstance(public_branch, dict) else None
                    )
                    app_name = app_data.get("common", {}).get("name")
                    installdir = app_data.get("config", {}).get("installdir")
                    header_url = (
                        f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid_str}/header.jpg"
                        if appid_str.isdigit()
                        else ""
                    )
                    has_depots_in_dlc = depots_raw.get("hasdepotsindlc") in (1, "1", True)
                    listofdlc = (
                        app_data.get("extended", {}).get("listofdlc")
                        or app_data.get("common", {}).get("listofdlc")
                        or app_data.get("listofdlc")
                    )
                    return {
                        "appid": appid_str,
                        "depots": depot_info,
                        "branches": branches_raw,
                        "installdir": installdir,
                        "header_url": header_url,
                        "buildid": build_id,
                        "timeupdated": time_updated,
                        "name": app_name,
                        "type": app_data.get("common", {}).get("type"),
                        "parent": app_data.get("common", {}).get("parent"),
                        "releasestate": app_data.get("common", {}).get("releasestate", "released"),
                        "hasdepotsindlc": has_depots_in_dlc,
                        "listofdlc": listofdlc,
                    }
            else:
                logger.debug(f"SteamCMD API returned HTTP {res.status_code} for AppID {appid_str} (attempt {attempt + 1}/{max_retries})")
        except Exception as exc:
            logger.debug(f"SteamCMD API request attempt {attempt + 1}/{max_retries} failed for AppID {appid_str}: {exc}")

        if attempt < max_retries - 1:
            time.sleep(0.3)

    return {}


def batched_fetch_steamcmd_info(*args, **kwargs) -> dict[str, Any]:  # pragma: no cover - removed API
    raise NotImplementedError("batched_fetch_steamcmd_info was removed; EXE-fingerprinter looks up one app at a time")
