"""
native_steam_handoff.py
=======================
Self-contained engine for handing off games directly to the Steam client.

Performs full registration in SLSsteam (AdditionalApps, AdditionalDepots,
DecryptionKeys, and optional ManifestIds for pinned builds) and signals
Steam to install the game natively, then returns control immediately to
EXE-fingerprinter without blocking the task runner.
"""

import logging
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

from core.native_steam.steam_manifest_pinning import (
    resolve_pinned_manifests,
    set_manifest_ids,
)

logger = logging.getLogger(__name__)

SLSSTEAM_API_PIPE = "/tmp/SLSsteam.API"
MAX_CONFIG_WAIT_SECONDS = 10
MAX_ACF_VERIFY_SECONDS = 10

# ---------------------------------------------------------------------------
# Shutdown coordination
# ---------------------------------------------------------------------------
_shutdown_flag = threading.Event()

# Registry of per-install cancel events: appid -> threading.Event
_retry_cancel_flags: Dict[str, Any] = {}
_retry_lock = threading.Lock()


def register_shutdown() -> None:
    """Signal all SLS background workers to stop. Call this on app exit."""
    _shutdown_flag.set()
    logger.info("SLSsteam integration: shutdown signalled — all retry workers will stop")


def _experimental_mode_enabled() -> bool:
    """True when the user opted into delegating manifest creation to Steam natively."""
    try:
        from utils.settings import get_settings

        return bool(get_settings().value("experimental_acf_independent", False, type=bool))
    except Exception:
        return False


def _is_slssteam_available() -> bool:
    """Check if SLSsteam API pipe exists (proxy for Steam + SLSsteam running)."""
    if sys.platform != "linux":
        return False
    return os.path.exists(SLSSTEAM_API_PIPE)


_proc_cache = {
    "last_check": 0.0,
    "steam_running": False,
    "slssteam_active": False,
}
_PROC_CACHE_TTL = 2.0  # seconds


def _update_proc_cache_if_needed() -> None:
    now = time.monotonic()
    if now - _proc_cache["last_check"] < _PROC_CACHE_TTL:
        return
    _proc_cache["last_check"] = now
    _proc_cache["steam_running"] = False
    _proc_cache["slssteam_active"] = False
    try:
        import subprocess as _sp

        out = _sp.run(["pgrep", "-x", "steam"], capture_output=True, text=True, timeout=2).stdout or ""
        _proc_cache["steam_running"] = len(out.strip()) > 0
        maps = "/proc"
        for pid_dir in os.listdir(maps):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/comm", "r", encoding="utf-8", errors="ignore") as f:
                    if f.read().strip() != "steam":
                        continue
                with open(f"/proc/{pid_dir}/maps", "r", encoding="utf-8", errors="ignore") as f:
                    if "SLSsteam.so" in f.read():
                        _proc_cache["slssteam_active"] = True
                        break
            except OSError:
                continue
    except Exception:
        pass


def is_steam_process_running() -> bool:
    """Check if the steam process is currently running on the system. Cached for 2 seconds."""
    _update_proc_cache_if_needed()
    return bool(_proc_cache["steam_running"])


def is_slssteam_process_active() -> bool:
    """Check if SLSsteam.so is loaded in Steam's memory space. Cached for 2 seconds."""
    _update_proc_cache_if_needed()
    return bool(_proc_cache["slssteam_active"])


def is_sls_filewatcher_dead() -> bool:
    """Check if SLSsteam's CFileWatcher thread crashed in the currently running Steam session.

    Reads the tail (up to 64KB) of ~/.SLSsteam.log in binary mode for
    b"Failed to read from FileWatcher". Because SLSsteam truncates ~/.SLSsteam.log
    on each Steam launch, any occurrence indicates the watcher is dead for the
    current session.

    Returns False immediately if Steam or SLSsteam is not running or log does not exist.
    Performance: <0.05ms execution time, zero system overhead.
    """
    try:
        if not is_steam_process_running():
            return False
        if not (_is_slssteam_available() or is_slssteam_process_active()):
            return False
        log_path = _get_sls_log_path()
        if not log_path.is_file():
            return False
        size = log_path.stat().st_size
        if size == 0:
            return False
        with open(log_path, "rb") as f:
            if size > 65536:
                f.seek(size - 65536)
            data = f.read()
            return b"Failed to read from FileWatcher" in data
    except Exception as e:
        logger.debug(f"is_sls_filewatcher_dead check error: {e}")
        return False


def notify_sls_watcher_dead(main_window: Optional[Any] = None) -> None:
    """Notify UI and status pager that the SLSsteam Filewatcher has crashed."""
    warning_msg = "SLSsteam Filewatcher crashed in Steam. Restart Steam"
    logger.warning(f"[SLSsteam] {warning_msg}")

    if main_window is None:
        try:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    if widget.__class__.__name__ == "MainWindow":
                        main_window = widget
                        break
        except Exception:
            pass

    if main_window is not None:
        try:
            if hasattr(main_window, "notify_sls_watcher_crashed_signal"):
                main_window.notify_sls_watcher_crashed_signal.emit()
            elif hasattr(main_window, "notify_sls_watcher_crashed"):
                main_window.notify_sls_watcher_crashed()
        except Exception as e:
            logger.debug(f"Failed to notify main_window of dead watcher: {e}")


def _get_sls_log_path() -> Path:
    """Return the SLSsteam log file path for the current Steam installation."""
    try:
        from core.steam_helpers import get_steam_env

        return get_steam_env().sls_log_path
    except Exception as e:
        logger.warning(f"_get_sls_log_path: SteamEnv unavailable ({e}), using native fallback")
        return Path.home() / ".SLSsteam.log"


def _get_steamapps_paths() -> List[Path]:
    """Return all steamapps directories to search for ACF manifests."""
    try:
        from core.steam_helpers import get_steam_env

        paths = get_steam_env().steamapps_paths
        if paths:
            return paths
    except Exception:
        pass
    fallback = []
    native = Path.home() / ".local" / "share" / "Steam" / "steamapps"
    flatpak = Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "steamapps"
    for p in (native, flatpak):
        if p.is_dir():
            fallback.append(p)
    return fallback


def _get_sls_log_size() -> int:
    """Return current SLS log file size for offset-based polling."""
    log_path = _get_sls_log_path()
    try:
        return log_path.stat().st_size if log_path.exists() else 0
    except (OSError, IOError):
        return 0


def _slssteam_api_send(command: str) -> bool:
    """Send a raw command to SLSsteam via the named pipe.

    Returns True on success.  Returns False (never raises) on any failure —
    including the pipe being gone because Steam/SLSsteam closed mid-write.
    """
    if not _is_slssteam_available():
        return False
    try:
        with open(SLSSTEAM_API_PIPE, "w") as f:
            f.write(command.strip() + "\n")
            f.flush()
        logger.info(f"SLSsteam API command sent: {command}")
        return True
    except OSError as e:
        logger.warning(
            f"SLSsteam API pipe write failed for '{command}': {e} "
            "(Steam/SLSsteam may have closed — command will be retried or skipped)"
        )
        return False


def _poll_sls_log_for(
    pattern: str,
    timeout_seconds: int = MAX_CONFIG_WAIT_SECONDS,
    start_offset: int = 0,
) -> bool:
    """Poll SLSsteam log for a regex pattern in NEW content only. Returns True if found before deadline."""
    log_path = _get_sls_log_path()
    if not log_path.exists():
        if _experimental_mode_enabled():
            logger.warning(
                f"SLSsteam log not found at {log_path}. Cannot poll for license events. Proceeding without confirmation."
            )
        return False

    compiled = re.compile(pattern)
    deadline = time.time() + timeout_seconds
    start = start_offset

    while time.time() < deadline:
        if _shutdown_flag.is_set():
            logger.debug("_poll_sls_log_for: shutdown flag set — aborting poll")
            return False
        try:
            current_size = log_path.stat().st_size
            if current_size > start:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(start)
                    for line in f:
                        if compiled.search(line):
                            return True
                start = current_size
        except (OSError, IOError):
            pass
        time.sleep(0.3)

    return False


def _verify_acf_created(appid: str, timeout: Optional[float] = None) -> bool:
    """Poll for ACF manifest creation by Steam across all known library paths."""
    acf_filename = f"appmanifest_{appid}.acf"
    steamapps_dirs = _get_steamapps_paths()
    candidate_paths = [sa / acf_filename for sa in steamapps_dirs if sa.is_dir()]

    if not candidate_paths:
        logger.warning(
            f"_verify_acf_created: no valid steamapps directories found to search for {appid}. "
            "Steam may not be installed or SteamEnv detection failed."
        )

    verify_seconds = timeout if timeout is not None else MAX_ACF_VERIFY_SECONDS
    deadline = time.time() + verify_seconds
    while time.time() < deadline:
        for p in candidate_paths:
            if p.exists():
                logger.info(f"Steam created ACF manifest for {appid} at {p}")
                return True
        if is_sls_filewatcher_dead():
            logger.info(f"_verify_acf_created: SLSsteam filewatcher is dead — breaking wait early")
            break
        time.sleep(0.5)

    searched = ", ".join(str(p) for p in candidate_paths)
    logger.warning(
        f"ACF manifest for {appid} not found after {verify_seconds}s. "
        f"Searched: {searched} — Steam may create it later"
    )
    return False


def _silent_background_retry_pipe(
    appid: str,
    library_index: int,
    max_retries: int = 3,
    library_path: str = "",
    main_window: Optional[Any] = None,
    **kwargs,
) -> None:
    """Start a daemon thread that retries the install pipe command if Steam was slow.

    Uses exponential backoff ([3, 6, 12] seconds by default).
    The worker stops as soon as any of the following is true:
      • The ACF file appears on disk (success — no more retries needed).
      • The per-install cancel event is set (uninstall_via_sls was called for this appid).
      • The global shutdown flag is set (application is closing).
      • SLSsteam pipe disappears or filewatcher crashes.
      • Retries are exhausted.

    In ACF-Independent mode, EXE-fingerprinter NEVER generates or writes a fallback ACF.
    Manifest creation is delegated entirely to Steam natively.
    """
    if max_retries != 3:
        backoff_intervals = [3 * (2**i) for i in range(max_retries)]
    else:
        backoff_intervals = [3, 6, 12]

    cancel_event = threading.Event()
    with _retry_lock:
        # Cancel any previous retry worker for this appid before starting a new one
        old_event = _retry_cancel_flags.get(appid)
        if old_event is not None:
            old_event.set()
        _retry_cancel_flags[appid] = cancel_event

    def _retry_worker() -> None:
        try:
            for i, wait_sec in enumerate(backoff_intervals):
                cancelled = cancel_event.wait(timeout=wait_sec)
                if cancelled or _shutdown_flag.is_set():
                    logger.info(
                        f"SLS retry worker for {appid}: "
                        f"{'cancelled (uninstall called)' if cancelled else 'app shutdown'} — stopping"
                    )
                    return

                if _verify_acf_created(appid, timeout=1.0):
                    logger.info(f"Silent retry: ACF confirmed for AppID {appid} on attempt {i + 1} — done")
                    return

                if not _is_slssteam_available():
                    logger.info(f"SLS retry worker for {appid}: SLSsteam pipe gone (Steam closed) — stopping retry worker")
                    return

                watcher_dead = is_sls_filewatcher_dead()
                if watcher_dead:
                    notify_sls_watcher_dead(main_window=main_window)
                    logger.warning(f"SLS retry worker for {appid}: watcher crashed — stopping retry worker")
                    return

                logger.info(
                    f"Silent background retry ({i + 1}/{len(backoff_intervals)}): "
                    f"resending install|{appid}|{library_index} after {wait_sec}s backoff..."
                )
                _slssteam_api_send(f"install|{appid}|{library_index}")

            # Final check after retry loop
            if _verify_acf_created(appid, timeout=1.5):
                logger.info(f"Silent retry: ACF confirmed for AppID {appid} after retry loop — done")
                return

            logger.info(
                f"SLS retry worker for {appid}: completed {len(backoff_intervals)} retries. "
                "Steam will discover and generate the manifest on next launch or restart."
            )
        finally:
            # Clean up the registry entry so it doesn't leak
            with _retry_lock:
                if _retry_cancel_flags.get(appid) is cancel_event:
                    del _retry_cancel_flags[appid]

    t = threading.Thread(target=_retry_worker, daemon=True, name=f"SLSRetry-{appid}")
    t.start()


def check_slssteam_active() -> bool:
    """Check if SLSsteam.so is loaded in a running Steam process."""
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/comm", "r", encoding="utf-8", errors="ignore") as f:
                    if f.read().strip() != "steam":
                        continue
            except OSError:
                continue
            try:
                with open(f"/proc/{pid_dir}/maps", "r", encoding="utf-8", errors="ignore") as f:
                    if "SLSsteam.so" in f.read():
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def get_sls_config_dir() -> Optional[Path]:
    """Return the active SLSsteam config directory."""
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env.sls_config_dir and env.sls_config_dir.is_dir():
            return env.sls_config_dir
    except Exception:
        pass

    flatpak = Path.home() / ".var/app/com.valvesoftware.Steam/.config/SLSsteam"
    native = Path.home() / ".config/SLSsteam"
    flatpak_steam = Path.home() / ".var/app/com.valvesoftware.Steam/.steam/steam"
    if flatpak_steam.is_dir() and flatpak.is_dir():
        return flatpak
    if native.is_dir():
        return native
    try:
        native.mkdir(parents=True, exist_ok=True)
        return native
    except OSError:
        return None


def get_primary_steamapps(dest_path: str) -> Optional[Path]:
    """Resolve steamapps directory for destination path."""
    if dest_path:
        candidate = Path(dest_path) / "steamapps"
        if candidate.is_dir():
            return candidate
        if Path(dest_path).name == "steamapps" and Path(dest_path).is_dir():
            return Path(dest_path)

    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env.steamapps_paths:
            return env.steamapps_paths[0]
    except Exception:
        pass

    for p in [
        Path.home() / ".local/share/Steam/steamapps",
        Path.home() / ".steam/steam/steamapps",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps",
    ]:
        if p.is_dir():
            return p
    return None


def resolve_library_index(dest_path: str) -> int:
    """Resolve Steam library folder index for dest_path."""
    try:
        from core.steam_helpers import get_library_index, find_steam_install
        steam_path = find_steam_install()
        return get_library_index(dest_path, steam_path)
    except Exception:
        return 0


def send_sls_api(command: str) -> bool:
    """Send command to the Steam-native install pipe (/tmp/SLSsteam.API)."""
    return _slssteam_api_send(command)


def deploy_bundled_plugins(plugins_dir: Path) -> bool:
    """Deploy the Katamari plugin family (interceptor, Styx, Katamari, Lobster, Lettuce) on demand."""
    try:
        from utils.plugin_manager import deploy_all_plugins
        ok, msgs = deploy_all_plugins(only_if_missing=True)  # never replace the user's files mid-download
        for m in msgs:
            logger.info(f"[SteamHandoff] {m}")
        return ok
    except Exception as e:
        logger.error(f"[SteamHandoff] Failed deploying plugins: {e}")
        return False


def get_depotcache_dirs(dest_path: str = "") -> List[Path]:
    """Resolve all active Steam depotcache directories."""
    dirs: List[Path] = []
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env.steam_path:
            p = Path(env.steam_path) / "depotcache"
            dirs.append(p)
    except Exception:
        pass

    for candidate in [
        Path.home() / ".local/share/Steam/depotcache",
        Path.home() / ".steam/steam/depotcache",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/depotcache",
    ]:
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)

    if dest_path:
        dp = Path(dest_path)
        for cand in [dp / "depotcache", dp.parent / "depotcache", dp.parent.parent / "depotcache"]:
            if cand.is_dir() and cand not in dirs:
                dirs.append(cand)

    return dirs


def sync_manifests_to_depotcache(appid: str, dest_path: str = "") -> int:
    """Extract all .manifest files for appid from Hubcap cache into Steam's depotcache.
    Pre-seeding Steam's depotcache allows Steam to install and verify depots locally
    without needing to query external MRC endpoints that may be Cloudflare-blocked.
    """
    try:
        from utils.helpers import get_base_path
        base_dir = Path(get_base_path())
    except Exception:
        base_dir = Path.home() / ".local" / "share" / "ACCELA"

    hubcap_dir = base_dir / "hubcap_manifests"
    if not hubcap_dir.exists():
        return 0

    depotcache_dirs = get_depotcache_dirs(dest_path)
    if not depotcache_dirs:
        return 0

    copied = 0
    for zip_path in hubcap_dir.glob(f"accela_fetch_{appid}*.zip"):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".manifest"):
                        data = zf.read(name)
                        base_name = os.path.basename(name)
                        for ddir in depotcache_dirs:
                            try:
                                ddir.mkdir(parents=True, exist_ok=True)
                                target = ddir / base_name
                                if not target.exists() or target.stat().st_size != len(data):
                                    target.write_bytes(data)
                                    copied += 1
                                    logger.info(f"[SteamHandoff] Synced manifest to depotcache: {target}")
                            except OSError as e:
                                logger.debug(f"[SteamHandoff] Failed writing manifest to {ddir}: {e}")
        except Exception as e:
            logger.debug(f"[SteamHandoff] Error reading zip {zip_path}: {e}")

    return copied


def perform_steam_handoff(
    game_data: Dict[str, Any],
    selected_depots: Optional[List[str]] = None,
    dest_path: str = "",
    progress_cb: Optional[Callable[[str], None]] = None,
    auto_install: bool = False,
) -> Tuple[bool, str]:
    """
    Perform the complete handoff flow to the Steam client:
      1. Verify Steam + SLSsteam active.
      2. Fetch depot keys & manifest GIDs (using cache / DB first).
      3. Resolve pinned manifests if applicable.
      4. Patch config.yaml atomically with AdditionalApps, AdditionalDepots, and DecryptionKeys.
      5. Deploy plugins.
      6. Wait for SLS license propagation.
      7. Remove stale stub ACF.
      8. If auto_install is True, signal Steam via install|<appid>|<library_index>.
         If auto_install is False (default), unlock in Steam library and return.
    """
    def _emit(msg: str):
        logger.info(f"[SteamHandoff] {msg}")
        if progress_cb:
            progress_cb(msg)

    appid = str(game_data.get("appid", "")).strip()
    game_name = game_data.get("game_name", f"App {appid}")

    if not appid or appid in ("0", "N/A", "unknown"):
        return False, "Invalid AppID provided."

    _emit(f"Initiating Steam handoff for {game_name} ({appid})...")

    # 1. Preflight
    if not check_slssteam_active():
        return False, "Steam is not running with SLSsteam active. Please start Steam first."

    sls_config_dir = get_sls_config_dir()
    if not sls_config_dir:
        return False, "SLSsteam config directory not found."

    steamapps_dir = get_primary_steamapps(dest_path)
    if not steamapps_dir:
        return False, f"Could not resolve steamapps directory for: {dest_path}"

    # 2. Check and clean stale stub ACF
    acf_path = steamapps_dir / f"appmanifest_{appid}.acf"
    if acf_path.exists():
        try:
            txt = acf_path.read_text(encoding="utf-8", errors="ignore")
            if '"buildid"\t\t"0"' in txt or '"buildid"\t"0"' in txt or '"StateFlags"\t\t"1026"' in txt:
                _emit(f"Cleaning up stale stub ACF: {acf_path}")
                acf_path.unlink()
        except OSError as e:
            logger.warning(f"[SteamHandoff] Error checking existing ACF: {e}")

    # 3. Retrieve depot keys and manifest GIDs (cache-first via _fetch_hubcap_keys)
    from core.tasks.native_steam_download_task import NativeSteamDownloadTask
    task_helper = NativeSteamDownloadTask()
    depot_keys, manifest_gids = task_helper._fetch_hubcap_keys(game_data, appid)

    if not depot_keys:
        return False, f"Cannot proceed with Steam handoff for {game_name} ({appid}): Missing Lua metadata and Hubcap API resolution failed."

    if game_data.get("game_name") and game_name.startswith("App "):
        game_name = game_data["game_name"]

    # Resolve depots if none explicitly selected
    from utils.dlc_helpers import is_dlc_only_mode, filter_dlc_depots_only
    is_dlc = is_dlc_only_mode(appid) or bool(game_data.get("is_dlc_only"))
    depots_meta = (game_data.get("depots") or {}) if game_data else {}

    if not selected_depots:
        all_d = set(str(d) for d in depot_keys.keys())
        if manifest_gids:
            all_d.update(str(d) for d in manifest_gids.keys())
        if game_data and game_data.get("depots"):
            all_d.update(str(d) for d in game_data["depots"].keys())
        if is_dlc:
            selected_depots = filter_dlc_depots_only(all_d, appid, depots_meta=depots_meta)
        else:
            selected_depots = [d for d in all_d if str(d) != str(appid)]
    elif is_dlc:
        selected_depots = filter_dlc_depots_only(selected_depots, appid, depots_meta=depots_meta)

    # Exclude any depots without decryption keys (except shared redists) to avoid Steam DepotWithoutKey errors
    from utils.plugin_games import SHARED_REDISTS
    valid_keyed_depots = {str(d) for d, k in depot_keys.items() if k and str(d) != str(appid)} | SHARED_REDISTS
    unkeyed_depots = [str(d) for d in selected_depots if str(d) not in valid_keyed_depots]
    if unkeyed_depots:
        _emit(f"Warning: Excluded {len(unkeyed_depots)} depot(s) without keys (e.g. blacklisted/unavailable): {unkeyed_depots}")
        logger.warning(f"[SteamHandoff] Excluded {len(unkeyed_depots)} unkeyed depot(s) for {appid}: {unkeyed_depots}")
        selected_depots = [d for d in selected_depots if str(d) in valid_keyed_depots]

    if not selected_depots and not is_dlc:
        return False, f"No valid keyed content depots could be resolved for {game_name} ({appid}). Steam cannot download without valid depots."

    # AdditionalDepots gets only actual depots. DecryptionKeys keeps depot keys AND the root AppID key.
    active_keys = {str(d): k for d, k in depot_keys.items()}
    if is_dlc and selected_depots:
        sel_set = {str(d) for d in selected_depots}
        active_keys = {d: k for d, k in active_keys.items() if str(d) in sel_set or str(d) == str(appid)}

    # 4. Check pinned manifests
    pinned_manifests = resolve_pinned_manifests(game_data, appid)
    if pinned_manifests:
        _emit(f"Pinning {len(pinned_manifests)} depot manifest(s) for build stability")

    # 5. Verify plugins are present BEFORE mutating configuration
    from utils.plugin_manager import are_plugins_present, is_plugin_check_bypassed
    if not are_plugins_present():
        return False, "AT0-M plugins (download interceptor / Styx) not found. Please enable and deploy plugin support from the AT0-M page in Settings, or enable 'I'm using custom plugins (Advanced)'."
    if is_plugin_check_bypassed() and not are_plugins_present(ignore_bypass=True):
        _emit("WARNING: Custom plugins active — third-party plugin support cannot be guaranteed.")

    # 6. Patch config.yaml atomically in a single pass
    config_path = sls_config_dir / "config.yaml"
    _emit("Patching SLSsteam config.yaml with game and depot keys...")

    # Record SLS log offset
    sls_log = sls_config_dir.parent / ".SLSsteam.log"
    if not sls_log.exists():
        sls_log = Path.home() / ".SLSsteam.log"
    log_offset = sls_log.stat().st_size if sls_log.exists() else 0

    from utils.dlc_helpers import get_all_dlcs_for_app
    discovered_dlcs = get_all_dlcs_for_app(appid, game_data, allow_network=not is_dlc)
    dlc_appids = [str(item["dlc_appid"]) for item in discovered_dlcs if item.get("dlc_appid")]
    if is_dlc:
        from utils.dlc_helpers import resolve_dlc_mapping_for_selection
        selected_dlc_apps, _ = resolve_dlc_mapping_for_selection(appid, selected_depots, game_data)
        dlc_appids = list(selected_dlc_apps)
    patch_ok = task_helper._patch_config(
        config_path, appid, game_name, active_keys, selected_depots=selected_depots,
        game_data=game_data, dlc_appids=dlc_appids,
    )
    if not patch_ok:
        return False, "Failed to patch SLSsteam config.yaml."

    # If pinned, write ManifestIds section
    if pinned_manifests:
        set_manifest_ids(config_path, pinned_manifests, comment=f"{game_name} Pinned Build")

    # 7. Wait for license event
    _emit("Waiting for Steam license propagation...")
    task_helper._sls_config_dir = sls_config_dir
    task_helper._poll_license_unlocked(appid, log_offset, timeout_sec=15.0)
    time.sleep(1.5)

    # 8. Sync manifests to Steam depotcache so Steam has them locally without needing MRC endpoints
    synced_mfs = sync_manifests_to_depotcache(appid, dest_path)
    if synced_mfs > 0:
        _emit(f"Synced {synced_mfs} manifest(s) into Steam depotcache.")

    # 9. Send install to Steam API only if auto_install is requested
    if auto_install:
        library_index = resolve_library_index(dest_path)
        _emit(f"Signalling Steam to install {game_name} into library folder {library_index}...")

        # Schedule smart background retry pipe to bridge the 15-20s Steam scheduler delay
        try:
            from core.native_steam.native_steam_handoff import _silent_background_retry_pipe

            _silent_background_retry_pipe(appid, library_index, max_retries=6, library_path=dest_path)
        except Exception as e:
            logger.debug(f"[SteamHandoff] Error scheduling background retry: {e}")

        sent_pipe = send_sls_api(f"install|{appid}|{library_index}")
        if not sent_pipe:
            logger.warning(f"[SteamHandoff] Named pipe send failed for {appid}; config is written, Steam will pick it up on start.")

        success_msg = (
            f"Successfully handed off {game_name} ({appid}) to Steam! "
            f"The download has been queued directly inside the Steam client."
        )
    else:
        success_msg = (
            f"Successfully added {game_name} ({appid}) to Steam! "
            f"The game and depot keys are unlocked in your Steam library, ready to install."
        )

    # Register into plugin_library so AT0-M and library management track it
    try:
        from utils.plugin_games import register_plugin_game
        depot_names: Dict[str, str] = {}
        depots_meta = (game_data.get("depots") or {}) if game_data else {}
        for did in (selected_depots or []):
            meta = depots_meta.get(did) or depots_meta.get(int(did) if str(did).isdigit() else did) or {}
            if isinstance(meta, dict) and meta.get("desc"):
                depot_names[str(did)] = meta["desc"]

        if not dlc_appids and is_dlc:
            try:
                from utils.dlc_helpers import get_dlc_only_info
                d_info = get_dlc_only_info(appid)
                if d_info:
                    dlc_appids = [str(x["dlc_appid"]) for x in d_info if x.get("dlc_appid")]
            except Exception:
                pass
            if not dlc_appids:
                for did in (selected_depots or []):
                    meta = depots_meta.get(did) or depots_meta.get(int(did) if str(did).isdigit() else did) or {}
                    if isinstance(meta, dict):
                        dlc_id = str(meta.get("dlcappid") or "").strip()
                        if dlc_id and dlc_id != appid and dlc_id not in dlc_appids:
                            dlc_appids.append(dlc_id)

        installdir = game_data.get("install_dir") or game_data.get("installdir") or ""
        register_plugin_game(
            appid=appid,
            name=game_name,
            depot_ids=selected_depots or list(depot_keys.keys()),
            decryption_keys=active_keys,
            installdir=installdir,
            depot_names=depot_names,
            dlc_appids=dlc_appids,
            is_dlc_only=is_dlc,
            app_token=game_data.get("app_token") or game_data.get("token"),
        )

        # Clean legacy ACCELA markers from install folder if any exist
        try:
            dest_lib = game_data.get("library_path") or ""
            if not dest_lib and "appmanifest_path" in game_data and game_data["appmanifest_path"]:
                dest_lib = str(Path(game_data["appmanifest_path"]).parent.parent)
            if installdir and dest_lib:
                game_folder = Path(dest_lib) / "steamapps" / "common" / installdir
                if game_folder.is_dir():
                    import shutil
                    for marker_name in (".exe_fingerprinter", ".EXE-FINGERPRINTER", ".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
                        m_path = game_folder / marker_name
                        if m_path.exists():
                            if m_path.is_dir():
                                shutil.rmtree(m_path, ignore_errors=True)
                            else:
                                m_path.unlink(missing_ok=True)
                            logger.info(f"[SteamHandoff] Cleaned legacy ACCELA marker {m_path}")
        except Exception as e:
            logger.debug(f"[SteamHandoff] Could not clean legacy marker: {e}")

        # Clean legacy .depot file and update status cache
        try:
            from utils.helpers import get_base_path
            depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
            if depot_file.exists():
                depot_file.unlink()
                logger.info(f"[SteamHandoff] Removed legacy depot file {depot_file}")
        except Exception as e:
            logger.debug(f"[SteamHandoff] Failed to remove legacy depot file: {e}")

        try:
            from utils.settings import get_settings
            settings = get_settings()
            settings.remove(f"game_update_status/{appid}")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[SteamHandoff] Could not register into plugin_library: {e}")

    _emit(success_msg)
    return True, success_msg
