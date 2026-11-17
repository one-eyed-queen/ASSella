"""Headless work behind the TUI: depot catalogue, .exe scan, and downloads.

Nothing here imports widgets, so it also backs the ``--api`` mode. The heavy
lifting (keys, manifest files, manifest-only dumps) is shared with the desktop
app through :mod:`core.manifest_dump`, which keeps the
two interfaces honest about which depots and files they see.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from core import exe_scan

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, int], None]  # message, step, total


@dataclass
class DepotPlan:
    depot_id: str
    manifest_id: str = ""
    name: str = ""
    size: int = 0
    keys_path: str = ""
    manifest_file: str = ""


@dataclass
class ScanResult:
    app_id: str
    app_name: str = ""
    entries: List[dict] = field(default_factory=list)
    plans: Dict[str, DepotPlan] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    key_dir: str = ""
    cancelled: bool = False

    def selected_by_depot(self, chosen: Iterable[dict]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}
        for entry in chosen:
            grouped.setdefault(str(entry["depot_id"]), []).append(entry["path"])
        for depot_id in grouped:
            grouped[depot_id] = sorted(grouped[depot_id], key=str.lower)
        return grouped


# ── depot catalogue ────────────────────────────────────────────────────────

def collect_depots(app_id: str) -> tuple[str, List[DepotPlan]]:
    """Windows-capable base-game depots, from the SteamCMD API.

    DLC depots are skipped: the tool fingerprints the base game, and a DLC
    depot's key is often not part of the base bundle.
    """
    from core.steam_api import fetch_steamcmd_info

    info = fetch_steamcmd_info(str(app_id)) or {}
    plans: List[DepotPlan] = []
    for depot_id, depot in (info.get("depots") or {}).items():
        if not str(depot_id).isdigit() or not isinstance(depot, dict):
            continue
        if depot.get("is_dlc"):
            continue
        oslist = (depot.get("oslist") or "").lower()
        if oslist and "windows" not in oslist and "all" not in oslist:
            continue
        manifest_id = str(depot.get("manifest_id") or "")
        if not manifest_id:
            continue
        plans.append(
            DepotPlan(
                depot_id=str(depot_id),
                manifest_id=manifest_id,
                name=depot.get("name") or "",
                size=int(depot.get("size") or 0),
            )
        )
    plans.sort(key=lambda p: (len(p.depot_id), p.depot_id))
    return info.get("name") or "", plans


def ensure_bundle(app_id: str, progress: Optional[Progress] = None) -> Optional[str]:
    """Make sure a Hubcap bundle exists locally; fetch it when missing."""
    from core.manifest_dump import ManifestDumpError, find_manifest_bundle

    try:
        return str(find_manifest_bundle(app_id))
    except ManifestDumpError:
        pass

    try:
        from core.morrenus_api import download_manifest

        if progress:
            progress("Fetching a Hubcap manifest bundle…", 0, 1)
        path, error = download_manifest(app_id)
        if path:
            return str(path)
        logger.warning("[TUI] Hubcap bundle unavailable for %s: %s", app_id, error)
    except Exception as exc:
        logger.warning("[TUI] Hubcap fetch failed for %s: %s", app_id, exc)
    return None


# ── scanning ───────────────────────────────────────────────────────────────

def _dotnet_and_dll() -> tuple[str, str]:
    from utils.helpers import get_dotnet_path, resource_path

    return (
        get_dotnet_path(),
        resource_path(os.path.join("deps", "DepotDownloader.dll")),
    )


def _private_key_dir() -> str:
    path = tempfile.mkdtemp(prefix="exefp-tui-keys-")
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def scan_app(
    app_id: str,
    progress: Optional[Progress] = None,
    cancel: Optional[threading.Event] = None,
) -> ScanResult:
    """Scan every Windows base-game depot for ``.exe`` files.

    Key order: your private API, then the local Hubcap bundle as a fallback.
    """
    from core.manifest_dump import prepare_manifest_dump

    result = ScanResult(app_id=str(app_id))
    result.key_dir = _private_key_dir()

    app_name, plans = collect_depots(app_id)
    result.app_name = app_name
    if not plans:
        result.errors.append(
            "SteamCMD returned no Windows depots for this AppID."
        )
        return result

    # API first: poll your private API once, up front. The Hubcap bundle is
    # only downloaded when the API does not know one or more depots — your
    # own service is free, the public Hubcap pool is not, and your fallback
    # key is rate-limited.
    from core.manifest_dump import _api_depot_map

    api_map, _api_error = _api_depot_map(app_id)
    needs_bundle = any(str(p.depot_id) not in api_map for p in plans)
    if needs_bundle:
        ensure_bundle(app_id, progress)
    dotnet, dll = _dotnet_and_dll()
    total = len(plans)

    for index, plan in enumerate(plans):
        if cancel and cancel.is_set():
            result.cancelled = True
            result.errors.append("Scan cancelled.")
            break

        if progress:
            progress(f"Preparing depot {plan.depot_id} ({index + 1}/{total})…", index, total)
        try:
            dump = prepare_manifest_dump(app_id, plan.depot_id)
        except Exception as exc:
            result.errors.append(f"Depot {plan.depot_id}: {exc}")
            continue

        # Keep a private copy of the single-line keys file: the shared
        # depot.keys in the temp dir is rewritten by the next depot.
        keys_path = os.path.join(result.key_dir, f"{plan.depot_id}.vdf")
        try:
            shutil.copyfile(dump.keys_path, keys_path)
            os.chmod(keys_path, 0o600)
        except OSError as exc:
            result.errors.append(f"Depot {plan.depot_id}: could not store its key ({exc}).")
            continue

        plan.manifest_id = dump.manifest_id
        plan.manifest_file = dump.manifest_file
        plan.keys_path = keys_path
        result.plans[plan.depot_id] = plan

        if progress:
            progress(f"Fetching manifest for depot {plan.depot_id}…", index, total)
        cmd = dump.command(dotnet, dll)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
                env={**os.environ, "DOTNET_NOLOGO": "1"},
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
                result.errors.append(
                    f"Depot {plan.depot_id}: DepotDownloader exited with "
                    f"code {proc.returncode}" + (f" ({tail[0]})" if tail else ".")
                )
                continue
        except (OSError, subprocess.SubprocessError) as exc:
            result.errors.append(f"Depot {plan.depot_id}: failed to run DepotDownloader ({exc}).")
            continue

        if not os.path.exists(dump.txt_path):
            result.errors.append(f"Depot {plan.depot_id}: no manifest listing was produced.")
            continue

        if progress:
            progress(f"Reading executables in depot {plan.depot_id}…", index, total)
        found = exe_scan.list_executables(dump.txt_path)
        for entry in found:
            result.entries.append(
                {
                    "depot_id": plan.depot_id,
                    "path": entry["path"],
                    "size": entry["size"],
                }
            )

    result.entries.sort(key=lambda e: (e["depot_id"], e["path"].lower()))
    return result


# ── downloading ────────────────────────────────────────────────────────────

def default_destination(app_id: str, base: Optional[str] = None) -> str:
    if base:
        return os.path.abspath(os.path.expanduser(base))
    from utils.helpers import get_exe_pawprint_dir

    return str(get_exe_pawprint_dir(app_id))


def download_selected(
    scan: ScanResult,
    chosen: Iterable[dict],
    destination: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[int, List[str]]:
    """Download only the chosen files, one DepotDownloader run per depot.

    Returns ``(downloaded_depots, errors)``.
    """
    grouped = scan.selected_by_depot(chosen)
    if not grouped:
        return 0, ["No executables selected."]

    dotnet, dll = _dotnet_and_dll()
    dest = destination or default_destination(scan.app_id)
    os.makedirs(dest, exist_ok=True)
    filelist_dir = tempfile.mkdtemp(prefix="exefp-tui-filelists-")
    downloaded = 0
    errors: List[str] = []

    try:
        for depot_id, paths in grouped.items():
            plan = scan.plans.get(str(depot_id))
            if not plan or not plan.keys_path:
                errors.append(f"Depot {depot_id}: no depot plan/key available.")
                continue
            if cancel and cancel.is_set():
                errors.append("Download cancelled.")
                break

            filelist_path = os.path.join(filelist_dir, f"filelist_{depot_id}.txt")
            try:
                with open(filelist_path, "w", encoding="utf-8") as handle:
                    for path in paths:
                        handle.write(path.replace("\\", "/") + "\n")
            except OSError as exc:
                errors.append(f"Depot {depot_id}: could not write its file list ({exc}).")
                continue

            cmd = [
                dotnet,
                dll,
                "-app", str(scan.app_id),
                "-depot", str(depot_id),
                "-manifest", plan.manifest_id,
            ]
            if plan.manifest_file and os.path.exists(plan.manifest_file):
                cmd += ["-manifestfile", plan.manifest_file]
            cmd += [
                "-depotkeys", plan.keys_path,
                "-filelist", filelist_path,
                "-dir", dest,
                "-max-downloads", "8",
            ]

            if progress:
                progress(f"Downloading {len(paths)} file(s) from depot {depot_id}…")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "DOTNET_NOLOGO": "1"},
                )
            except OSError as exc:
                errors.append(f"Depot {depot_id}: could not start DepotDownloader ({exc}).")
                continue

            assert proc.stdout is not None
            last_line = ""
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                last_line = line
                if progress:
                    progress(f"Depot {depot_id}: {line[:160]}")
                if cancel and cancel.is_set():
                    proc.terminate()
                    break
            returncode = proc.wait()
            if returncode != 0:
                errors.append(
                    f"Depot {depot_id}: DepotDownloader exited with code {returncode}"
                    + (f" ({last_line[:160]})" if last_line else ".")
                )
            else:
                downloaded += 1
    finally:
        shutil.rmtree(filelist_dir, ignore_errors=True)

    return downloaded, errors


def cleanup_keys(scan: ScanResult) -> None:
    """Remove the private key copies once a download has finished."""
    if scan.key_dir:
        shutil.rmtree(scan.key_dir, ignore_errors=True)
        scan.key_dir = ""
