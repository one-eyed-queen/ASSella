"""Prepare a depot's manifest so that individual files can be picked out of it.

Customising files starts from a manifest: the app's newest Hubcap bundle is
extracted, the depot key comes from the bundle's LUA file (falling back to the
local key database), and DepotDownloader is pointed at the ``.manifest`` inside
it to write a text listing of the depot's contents.

None of that needs a widget, so it lives here rather than in the dialog. Every
failure is a :class:`ManifestDumpError` whose message is what the dialog shows,
which is why the messages read like user-facing text.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from utils.lua_parsing import find_live

logger = logging.getLogger(__name__)

AppId = Union[int, str]


class ManifestDumpError(Exception):
    """A depot's manifest could not be prepared for file-level selection."""


@dataclass(frozen=True)
class ManifestDump:
    """A prepared dump: where its files are, and how to turn them into a list."""

    app_id: AppId
    depot_id: str
    manifest_id: str
    manifest_file: str
    keys_path: str
    temp_dir: str
    txt_path: str

    def command(self, dotnet_path: str, dll_path: str) -> list:
        """The DepotDownloader invocation that writes the file listing.

        ``-manifestfile`` is only passed when the bundle actually carried a
        ``.manifest``; without one DepotDownloader fetches the manifest itself
        from the Steam CDN using the manifest id.
        """
        cmd = [
            dotnet_path,
            dll_path,
            "-app", str(self.app_id),
            "-depot", str(self.depot_id),
            "-manifest", str(self.manifest_id),
        ]
        if self.manifest_file:
            cmd += ["-manifestfile", self.manifest_file]
        cmd += [
            "-depotkeys", self.keys_path,
            "-manifest-only",
            "-dir", self.temp_dir,
        ]
        return cmd


def find_manifest_bundle(app_id: AppId, manifests_dir: Optional[Path] = None) -> Path:
    """The newest Hubcap bundle for ``app_id``.

    Two names are in circulation - ``accela_fetch_<app>.zip`` for the plain
    fetch and ``accela_fetch_<app>_<something>.zip`` for the variants - and the
    most recently written one wins.
    """
    if manifests_dir is None:
        from utils.helpers import get_base_path

        manifests_dir = get_base_path() / "hubcap_manifests"

    zips = list(manifests_dir.glob(f"accela_fetch_{app_id}.zip")) + list(
        manifests_dir.glob(f"accela_fetch_{app_id}_*.zip")
    )
    if not zips:
        raise ManifestDumpError(
            f"No manifest zip file found for AppID {app_id} in {manifests_dir}."
        )
    zips.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return zips[0]


def extract_bundle(zip_path: str, temp_dir: str) -> None:
    """Unpack a Hubcap bundle into ``temp_dir``."""
    try:
        from utils.steam_manifest import safe_extract_zip

        safe_extract_zip(zip_path, temp_dir)
    except Exception as e:
        raise ManifestDumpError(f"Failed to extract manifest zip: {e}") from e


_API_CACHE: Dict[str, tuple] = {}
_API_CACHE_TTL = 300.0
_API_TIMEOUT = 6.0


def _api_depot_map(app_id: AppId):
    """Depot keys and manifests from the private API, cached for a few minutes.

    Returns ``(mapping, error)``. ``error`` is a short, user-facing reason the
    API produced nothing — empty when the API was reached and answered (even
    with no known depots, which is not an error). The API is optional: when the
    user has not signed in, or the service is unreachable, this returns
    ``({}, reason)`` so callers can say *why* instead of blaming every source.
    """
    cache_key = str(app_id)
    now = time.monotonic()
    cached = _API_CACHE.get(cache_key)
    if cached and now - cached[0] < _API_CACHE_TTL:
        return cached[1], cached[2]

    mapping: Dict[str, Dict[str, str]] = {}
    error = ""
    try:
        from core import exe_fingerprinter_api as api

        if not api.is_configured():
            error = (
                "not signed in — open Settings, EXE-fingerprinter account, and sign in "
                "(or set a working Hubcap key instead)"
            )
        else:
            result = api.depots(int(app_id), timeout=_API_TIMEOUT)
            if not result.ok:
                detail = " ".join((result.error or "no details").split())[:200]
                error = f"sign-in or lookup failed ({detail})"
                logger.debug("[ManifestDump] API depot lookup failed: %s", result.error)
            elif not isinstance(result.data, dict):
                error = "the API returned an unexpected response"
            else:
                mapping = {
                    str(entry.get("depot_id")): {
                        "key": entry.get("depot_key") or "",
                        "manifest": str(entry.get("manifest_id") or ""),
                    }
                    for entry in result.data.get("depots", [])
                    if entry.get("depot_id")
                }
                if not mapping:
                    error = "the API does not know this game's depots (it may not list it yet)"
    except (ImportError, ValueError, TypeError) as exc:
        logger.debug("[ManifestDump] API depot lookup unavailable: %s", exc)
        error = "the API client is unavailable"

    _API_CACHE[cache_key] = (now, mapping, error)
    return mapping, error


def read_depot_key(
    temp_dir: str,
    depot_id: str,
    app_id: AppId,
    key_lookup: Optional[Callable[[str], Optional[str]]] = None,
    use_api: bool = True,
) -> Optional[str]:
    """The depot key, from the bundle's LUA file or the local key database.

    Bundles generated from a depot key request carry the key in their LUA file,
    either as an ``addappid`` line or as a ``["depot"] = "key"`` entry; a
    smart-generated bundle carries none, so the local database is tried too.

    ``key_lookup`` replaces the database for one call; it is given the depot id
    and returns the key or None. When everything local misses and ``use_api``
    is set, the private API is asked as a last resort.
    """
    try:
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            lua_content = Path(lua_files[0]).read_text(encoding="utf-8", errors="ignore")
            match = find_live(
                lua_content,
                r"addappid\(\s*"
                + re.escape(depot_id)
                + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)",
            )
            if not match:
                match = re.search(
                    rf'\["?{re.escape(depot_id)}"?\]\s*=\s*"(.*?)"', lua_content
                )
            if match:
                return match.group(1).strip()
    except Exception as e:
        logger.warning(f"Failed to parse LUA for depot keys: {e}")

    try:
        if key_lookup is not None:
            cached = key_lookup(depot_id)
        else:
            from core.depot_key_manager import DepotKeyManager

            cached = DepotKeyManager().get_depot_keys(app_id).get(depot_id)
        if cached:
            return cached
    except Exception as dkm_e:
        logger.warning(
            f"Failed to load key from depot_keys.db for depot {depot_id}: {dkm_e}"
        )

    if use_api and key_lookup is None:
        api_map, _api_error = _api_depot_map(app_id)
        api_key = api_map.get(str(depot_id), {}).get("key")
        if api_key:
            logger.info("[ManifestDump] Depot %s key served by the private API", depot_id)
            return api_key

    return None


def find_manifest_file(temp_dir: str, depot_id: str) -> Path:
    """The bundle's ``.manifest`` for ``depot_id``, or the only one there is."""
    manifest_files = list(Path(temp_dir).glob(f"{depot_id}_*.manifest"))
    if not manifest_files:
        candidates = list(Path(temp_dir).glob("*.manifest"))
        # Only accept an un-prefixed bundle when there is exactly one file:
        # the temp dir is reused across dumps, so picking any manifest would
        # risk attaching another depot's manifest to this one.
        if len(candidates) == 1:
            manifest_files = candidates
    if not manifest_files:
        raise ManifestDumpError(
            f"No manifest file (*.manifest) found for depot {depot_id} in the extracted manifest bundle."
        )
    return manifest_files[0]


def manifest_id_from_name(name: str) -> str:
    """``123_456789.manifest`` -> ``456789``; a bare stem is its own id."""
    stem = name.replace(".manifest", "")
    return stem.split("_", 1)[1] if "_" in stem else stem


def write_keys_file(keys_path: str, depot_id: str, depot_key: str) -> None:
    """Write the single-line ``depot.keys`` DepotDownloader reads."""
    try:
        with open(keys_path, "w") as kf:
            kf.write(f"{depot_id};{depot_key}\n")
    except OSError as e:
        raise ManifestDumpError(f"Failed to write keys file: {e}") from e


def discard_keys_file(keys_path: str) -> None:
    """Drop a ``depot.keys`` we wrote; the key itself is not ours to keep."""
    if os.path.exists(keys_path):
        try:
            os.remove(keys_path)
        except OSError:
            pass


def prepare_manifest_dump(
    app_id: AppId,
    depot_id: str,
    *,
    manifests_dir: Optional[Path] = None,
    temp_root: Optional[str] = None,
    key_lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> ManifestDump:
    """Do everything up to running DepotDownloader for ``depot_id``.

    The optional arguments exist so this can be exercised against a scratch
    directory; the dialog passes none of them.

    Order of attempts, cheapest first: the private API, then the local Hubcap
    bundle and key database. When the API knows the depot, its key and
    manifest id are enough for DepotDownloader to fetch the manifest from
    Steam itself, and nothing is downloaded from Hubcap at all.
    """
    # API first: your private API serves the base-game keys and manifest GIDs.
    api_error = ""
    if key_lookup is None:
        api_map, api_error = _api_depot_map(app_id)
        api_info = api_map.get(str(depot_id)) or {}
        if api_info.get("key") and api_info.get("manifest"):
            temp_dir = os.path.join(
                temp_root or tempfile.gettempdir(), f"selective_manifests_{app_id}"
            )
            os.makedirs(temp_dir, exist_ok=True)
            keys_path = os.path.join(temp_dir, "depot.keys")
            write_keys_file(keys_path, depot_id, api_info["key"])
            return ManifestDump(
                app_id=app_id,
                depot_id=depot_id,
                manifest_id=str(api_info["manifest"]),
                manifest_file="",
                keys_path=keys_path,
                temp_dir=temp_dir,
                txt_path=os.path.join(temp_dir, f"manifest_{depot_id}_{api_info['manifest']}.txt"),
            )

    bundle_error = ""
    try:
        zip_path: Optional[Path] = find_manifest_bundle(app_id, manifests_dir)
    except ManifestDumpError as exc:
        zip_path = None
        bundle_error = str(exc)

    temp_dir = os.path.join(
        temp_root or tempfile.gettempdir(), f"selective_manifests_{app_id}"
    )
    os.makedirs(temp_dir, exist_ok=True)
    if zip_path is not None:
        extract_bundle(str(zip_path), temp_dir)

    depot_key = read_depot_key(temp_dir, depot_id, app_id, key_lookup)
    if not depot_key:
        parts = [f"No depot key for depot {depot_id}:"]
        if api_error:
            parts.append(f"your EXE-fingerprinter API — {api_error};")
        else:
            parts.append(f"your EXE-fingerprinter API answered but does not know depot {depot_id};")
        if bundle_error:
            parts.append(f"Hubcap bundle — {bundle_error};")
        elif zip_path is not None:
            parts.append("Hubcap bundle was fetched but carries no key for this depot;")
        else:
            parts.append("Hubcap bundle — not available (check your Hubcap key in Settings);")
        parts.append("local key database — no entry.")
        raise ManifestDumpError(" ".join(parts))

    keys_path = os.path.join(temp_dir, "depot.keys")
    write_keys_file(keys_path, depot_id, depot_key)

    manifest_file = ""
    try:
        manifest_path = find_manifest_file(temp_dir, depot_id)
        manifest_file = str(manifest_path)
        manifest_id = manifest_id_from_name(manifest_path.name)
    except ManifestDumpError:
        # No local .manifest: the API's manifest id lets DepotDownloader fetch
        # it by itself. Without that there is nothing usable to scan.
        api_manifest = (
            _api_depot_map(app_id)[0].get(str(depot_id), {}).get("manifest", "")
            if key_lookup is None
            else ""
        )
        if not api_manifest:
            # A key on disk outlives the dialog, so never leave one behind for
            # a dump that cannot be used.
            discard_keys_file(keys_path)
            raise
        manifest_id = str(api_manifest)

    return ManifestDump(
        app_id=app_id,
        depot_id=depot_id,
        manifest_id=manifest_id,
        manifest_file=manifest_file,
        keys_path=keys_path,
        temp_dir=temp_dir,
        txt_path=os.path.join(temp_dir, f"manifest_{depot_id}_{manifest_id}.txt"),
    )
