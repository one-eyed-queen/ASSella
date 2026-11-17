"""Qt-free helpers for EXE-fingerprinter.

Paths are platform-aware; the app's data root follows the rebrand
(``~/.local/share/EXE-fingerprinter`` on Linux). A one-time migration
renames the legacy ``ACCELA`` folder. ``get_exe_pawprint_dir`` returns the
folder where downloaded ``.exe`` files land (``%APPDATA%\\exe-pawprint``
on Windows).
"""
import logging
import os
import platform
import ssl
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_exe_pawprint_dir(app_id: str | int | None = None) -> Path:
    """Where downloaded ``.exe`` files go (``%APPDATA%/exe-pawprint/<appid>`` on Windows)."""
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        root = base / "exe-pawprint"
    elif system == "darwin":
        root = Path.home() / "Library" / "Application Support" / "exe-pawprint"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "exe-pawprint"
    root.mkdir(parents=True, exist_ok=True)
    if app_id is None:
        return root
    target = root / str(app_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _get_user_dotnet_path() -> str:
    """Get the path to user's .dotnet directory, platform-aware.

    For Windows, uses LocalAppData (default for dotnet-install.ps1).
    For Linux/macOS, uses HOME/.dotnet.
    """
    if sys.platform == "win32":
        # On Windows, dotnet-install.ps1 defaults to
        # %LocalAppData%\Microsoft\dotnet
        local_app_data = os.environ.get(
            "LOCALAPPDATA", os.path.expandvars("%LocalAppData%")
        )
        return os.path.join(local_app_data, "Microsoft", "dotnet", "dotnet")
    else:
        # On Linux/macOS, use HOME/.dotnet
        return os.path.expanduser("~/.dotnet/dotnet")




def is_arm64() -> bool:
    """Check if the current host architecture is ARM64 / aarch64.

    Can be simulated on x86_64 machines via EXE_FINGERPRINTER_FORCE_ARM64=1.
    """
    if os.environ.get("EXE_FINGERPRINTER_FORCE_ARM64") in ("1", "true", "True"):
        return True
    return platform.machine().lower() in ("aarch64", "arm64", "armv8b", "armv8l")




def _get_user_dotnet_root() -> str:
    """Get the path to user's .dotnet root directory, platform-aware.

    For Windows, uses LocalAppData (default for dotnet-install.ps1).
    For Linux/macOS, uses HOME/.dotnet.
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get(
            "LOCALAPPDATA", os.path.expandvars("%LocalAppData%")
        )
        return os.path.join(local_app_data, "Microsoft", "dotnet")
    else:
        return os.path.expanduser("~/.dotnet")




def get_dotnet_env() -> dict[str, str]:
    """Get sanitized environment for running .NET processes.

    Cleans AppImage library overrides that break .NET runtime host and sets
    DOTNET_ROOT and globalization invariant for SteamOS / minimal Linux.
    """
    from pathlib import Path

    env = os.environ.copy()

    # 1. Clean AppImage library overrides that break .NET runtime host
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)

    # 2. Find local or system .dotnet directory
    local_dotnet = Path.home() / ".dotnet"
    system_dotnet = Path("/usr/share/dotnet")
    usr_lib_dotnet = Path("/usr/lib/dotnet")

    dotnet_dir = None
    if local_dotnet.exists():
        dotnet_dir = local_dotnet
    elif system_dotnet.exists():
        dotnet_dir = system_dotnet
    elif usr_lib_dotnet.exists():
        dotnet_dir = usr_lib_dotnet

    if dotnet_dir:
        # Set DOTNET_ROOT (required for .NET to find runtimes)
        env["DOTNET_ROOT"] = str(dotnet_dir)
        # Prepend to PATH so dotnet executable is found if needed
        env["PATH"] = f"{dotnet_dir}:{env.get('PATH', '')}"

    # Prevent ICU globalization missing crashes on SteamOS/minimal Linux
    env.setdefault("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")

    return env




def _clean_corrupt_hostfxr(dotnet_root: str):
    """Detect and clean ghost/corrupted host/fxr directories that break .NET host.

    If a subdirectory in host/fxr is missing libhostfxr.so/hostfxr.dll, the .NET muxer
    will pick it as the latest version and crash with code 131 (0x80008083).
    """
    try:
        fxr_dir = os.path.join(dotnet_root, "host", "fxr")
        if not os.path.isdir(fxr_dir):
            return
        lib_name = "hostfxr.dll" if sys.platform == "win32" else "libhostfxr.so"
        for item in os.listdir(fxr_dir):
            item_path = os.path.join(fxr_dir, item)
            if os.path.isdir(item_path):
                target_lib = os.path.join(item_path, lib_name)
                if not os.path.isfile(target_lib):
                    logger.warning(
                        f"[.NET Auto-Repair] Found corrupt host/fxr directory missing {lib_name}: {item_path}. "
                        "Removing it to restore .NET functionality."
                    )
                    shutil.rmtree(item_path, ignore_errors=True)
    except Exception as e:
        logger.debug(f"Error during host/fxr cleanup: {e}")




def dotnet_has_compatible_runtime(list_runtimes_output: str) -> bool:
    """True when --list-runtimes output contains .NET 9 or newer.

    DepotDownloader targets net9.0 with rollForward=LatestMajor, so any
    Microsoft.NETCore.App of version 9.x or higher can run it.
    """
    for line in (list_runtimes_output or "").splitlines():
        match = re.search(r"Microsoft\.NETCore\.App\s+(\d+)\.", line)
        if match and int(match.group(1)) >= 9:
            return True
    return False




def get_dotnet_path() -> str | None:
    """Get the path to dotnet executable, checking both locations.

    Prefers user-local and Program Files locations, then PATH.
    """
    candidates = []

    system_dotnet = shutil.which("dotnet")
    logger.debug(f"System dotnet from PATH: {system_dotnet}")
    if system_dotnet:
        candidates.append(system_dotnet)

    user_dotnet = _get_user_dotnet_path()

    if sys.platform == "win32":
        if user_dotnet.lower().endswith("dotnet.exe"):
            user_dotnet_exe = user_dotnet
        else:
            user_dotnet_exe = user_dotnet + ".exe"
    else:
        user_dotnet_exe = user_dotnet

    candidates.append(user_dotnet_exe)

    # Deduplicate candidates while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    for dotnet_exe in unique_candidates:
        try:
            dotnet_exe = str(dotnet_exe)
            dotnet_root = os.path.dirname(dotnet_exe)
            _clean_corrupt_hostfxr(dotnet_root)
            env = get_dotnet_env()
            env.setdefault("DOTNET_ROOT", dotnet_root)
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 10,
                "env": env,
            }
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            result = subprocess.run([dotnet_exe, "--list-runtimes"], **run_kwargs)
            if dotnet_has_compatible_runtime(result.stdout):
                logger.info(f"Found a compatible .NET runtime using {dotnet_exe}")
                return dotnet_exe
            elif result.returncode != 0:
                logger.debug(
                    f"Probing {dotnet_exe} failed (code {result.returncode}): {result.stderr.strip()}"
                )
            else:
                logger.debug(
                    f"Probing {dotnet_exe} succeeded but no .NET 9+ runtime in: {result.stdout.strip()}"
                )

        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"Error probing {dotnet_exe}: {e}")

    return None




def install_dotnet_9() -> bool:
    """Install .NET 9 runtime using official installer script."""
    try:
        if sys.platform == "win32":
            return _install_dotnet_9_windows()
        else:
            return _install_dotnet_9_linux()
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Error installing .NET 9: {e}")
        return False




def _install_dotnet_9_linux() -> bool:
    """Install .NET 9 runtime on Linux using official installer script."""
    try:
        logger.info("Installing .NET 9 runtime via official installer script...")

        # Set DOTNET_ROOT to ensure the install script uses and exposes
        # the correct location
        env = get_dotnet_env()
        dotnet_root = _get_user_dotnet_root()
        env["DOTNET_ROOT"] = dotnet_root

        # Download and run the install script using the official method
        install_script_path = os.path.join(dotnet_root, "dotnet-install.sh")

        # Create the dotnet root directory if it doesn't exist
        os.makedirs(dotnet_root, exist_ok=True)

        # Download the script first
        logger.info("Downloading .NET 9 installer script...")
        download_cmd = None
        if shutil.which("curl"):
            download_cmd = [
                "curl",
                "-sSL",
                "-o",
                install_script_path,
                "https://dot.net/v1/dotnet-install.sh",
            ]
        elif shutil.which("wget"):
            download_cmd = [
                "wget",
                "-q",
                "-O",
                install_script_path,
                "https://dot.net/v1/dotnet-install.sh",
            ]

        if not download_cmd:
            logger.error(
                "Neither curl nor wget is available to download the "
                ".NET installer script"
            )
            return False

        download_cmd_str: list[str] = [str(part) for part in download_cmd]
        download_result = subprocess.run(
            download_cmd_str,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if download_result.returncode != 0:
            logger.error(
                f"Failed to download .NET 9 installer script: {download_result.stderr}"
            )
            return False

        # Make it executable and run it
        os.chmod(install_script_path, 0o755)

        logger.info("Running .NET 9 installer script...")
        install_result = subprocess.run(
            [
                str(install_script_path),
                "--channel",
                "9.0",
                "--runtime",
                "dotnet",
                "--install-dir",
                str(dotnet_root),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        # Cleanup script
        try:
            os.remove(install_script_path)
        except OSError:
            pass

        if install_result.returncode == 0:
            logger.info(".NET 9 runtime installed successfully")
            logger.debug(f"Install stdout: {install_result.stdout}")
            return True

        logger.error(
            f"Failed to install .NET 9 (exit code {install_result.returncode})"
        )
        logger.error(f"stdout: {install_result.stdout}")
        logger.error(f"stderr: {install_result.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Timeout while installing .NET 9")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Exception during .NET 9 installation: {e}")
        return False




def _install_dotnet_9_windows() -> bool:
    """Install .NET 9 runtime on Windows using official installer script."""
    max_retries = 2
    dotnet_root = _get_user_dotnet_root()

    install_script_url = "https://dot.net/v1/dotnet-install.ps1"

    for attempt in range(max_retries):
        script_path = None
        try:
            logger.info(
                f"Installing .NET 9 runtime (attempt {attempt + 1}/{max_retries})..."
            )

            env = os.environ.copy()
            env["DOTNET_ROOT"] = dotnet_root

            with tempfile.NamedTemporaryFile(
                suffix=".ps1", delete=False, mode="wb"
            ) as tmp_script:
                script_path = tmp_script.name
                logger.info("Downloading installer script via Python...")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(
                    install_script_url, context=ctx, timeout=30
                ) as response:
                    tmp_script.write(response.read())

            logger.info("Running .NET 9 installer script...")

            install_cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-Channel",
                "9.0",
                "-Runtime",
                "dotnet",
                "-InstallDir",
                dotnet_root,
            ]

            install_cmd_str: list[str] = [str(part) for part in install_cmd]
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 300,
                "env": env,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            install_result = subprocess.run(install_cmd_str, **run_kwargs)

            if install_result.returncode == 0:
                logger.info(".NET 9 runtime installed successfully.")
                dotnet_exe = os.path.join(dotnet_root, "dotnet.exe")
                if os.path.exists(dotnet_exe):
                    logger.debug(f"Confirmed executable exists at: {dotnet_exe}")
                    return True
                else:
                    logger.debug(
                        "Installer finished code 0 but dotnet.exe not "
                        "found in target dir."
                    )

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing .NET 9")
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"Error during .NET 9 installation: {e}")
        finally:
            if script_path and os.path.exists(script_path):
                os.remove(script_path)

        if attempt < max_retries - 1:
            logger.info("Retrying installation...")

    return False




def ensure_dotnet_availability() -> bool:
    """Ensure .NET 9 runtime is available, install if missing."""
    _clean_corrupt_hostfxr(_get_user_dotnet_root())
    if get_dotnet_path():
        return True

    logger.warning(".NET 9 not found, attempting automatic installation...")
    success = install_dotnet_9()

    if success:
        dotnet_exec = get_dotnet_path()
        if dotnet_exec:
            dotnet_root = os.path.dirname(dotnet_exec)
            try:
                os.environ["DOTNET_ROOT"] = dotnet_root
                current_path = os.environ.get("PATH", "")
                if dotnet_root not in current_path.split(os.pathsep):
                    os.environ["PATH"] = dotnet_root + os.pathsep + current_path
                logger.debug(
                    ".NET 9 is now available and DOTNET_ROOT/PATH set for this process"
                )
            except RuntimeError:
                logger.debug(
                    "Failed to set DOTNET_ROOT/PATH in current process environment"
                )
            return True
        logger.warning(".NET 9 installation completed but still not detected")
    logger.error("Failed to ensure .NET 9 availability")
    return False




def get_base_path(app_name: str = "EXE-fingerprinter") -> Path:
    """Return the base directory for the current platform (no logs dir)."""
    try:
        _rename_legacy_base()
    except Exception:
        pass
    system = platform.system().lower()

    if system == "linux":
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / app_name

        home = os.environ.get("HOME")
        if home:
            return Path(home) / ".local" / "share" / app_name

        tilde = os.path.expanduser("~")
        if tilde not in ("~", ""):  # ensures it actually expanded
            return Path(tilde) / ".local" / "share" / app_name

        # If all fails resort to same dir save
        return Path(".") / app_name

    elif system == "windows":
        # Using AppData/Roaming instead of program directory
        appdata = os.environ.get("APPDATA")
        if appdata:
            base_path = Path(appdata) / app_name

            # Check for existing ACCELA folder in program directory and move it
            old_path = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / app_name
            if old_path.exists() and not base_path.exists():
                try:
                    shutil.move(str(old_path), str(base_path))
                except OSError as e:
                    logger.warning(f"Could not move existing data: {e}")

            return base_path
        else:
            # Fallback to program directory if APPDATA not found
            return Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / app_name

    elif system == "darwin":  # macOS
        # Standard macOS location
        return Path.home() / "Library" / "Logs" / app_name

    else:
        # Fallback directory for unknown platforms
        return Path.home() / ".logs" / app_name





def _rename_legacy_base() -> None:
    """One-time rename of the old ACCELA data folder to the EXE-fingerprinter one."""
    try:
        import shutil
        system = platform.system().lower()
        if system == "windows":
            base = Path(os.environ.get("APPDATA", "")) / "EXE-fingerprinter"
        elif system == "darwin":
            base = Path.home() / "Library" / "Logs" / "EXE-fingerprinter"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "EXE-fingerprinter"
        new = Path(str(base).replace("EXE-fingerprinter", "EXE-fingerprinter"))
        if base.exists() and not new.exists():
            shutil.move(str(base), str(new))
            logger.info(f"Renamed legacy data folder {base} -> {new}")
    except Exception as exc:
        logger.debug(f"Legacy data folder rename skipped: {exc}")


def get_cache_dir(subfolder: str = "") -> Path:
    """Return cache directory (~/.local/share/ACCELA/cache/[subfolder]).
    Checks legacy location first to maintain 100% backward compatibility.
    """
    base = get_base_path()
    if subfolder:
        legacy_path = base / subfolder
        if legacy_path.exists():
            return legacy_path
        new_path = base / "cache" / subfolder
        new_path.mkdir(parents=True, exist_ok=True)
        return new_path
    c_path = base / "cache"
    c_path.mkdir(parents=True, exist_ok=True)
    return c_path




def get_manifests_dir(subfolder: str = "") -> Path:
    """Return manifests directory (~/.local/share/ACCELA/manifests/[subfolder]).
    Checks legacy location first to maintain 100% backward compatibility.
    """
    base = get_base_path()
    if subfolder:
        legacy_path = base / subfolder
        if legacy_path.exists():
            return legacy_path
        new_path = base / "manifests" / subfolder
        new_path.mkdir(parents=True, exist_ok=True)
        return new_path
    m_path = base / "manifests"
    m_path.mkdir(parents=True, exist_ok=True)
    return m_path




def get_data_dir() -> Path:
    """Return data directory (~/.local/share/ACCELA/data/)."""
    base = get_base_path()
    d_path = base / "data"
    d_path.mkdir(parents=True, exist_ok=True)
    return d_path


DB_RELOCATED_FILES = {
    "depot_keys.db",
    "denuvo_cache.json",
    "denuvo_cache.db",
    "install_history.json",
    "protondb_cache.json",
    "protondb_cache.db",
    "update_status_cache.json",
    "update_status_cache.db",
    "workshop_cache.db",
    "steam_headers.db",
    "steamdb_builds.db",
    "games_cache.json",
    "plugin_library.json",
    "voices.json",
    "games.db",
    "exe_fingerprinter.db",
}




def get_data_file_path(filename: str) -> Path:
    """Return path for a database or state file (e.g. steam_headers.db, install_history.json).
    Checks new db/ folder under base_path first; if not found, falls back to legacy locations.
    """
    base = get_base_path()
    if filename in DB_RELOCATED_FILES:
        db_dir = base / "db"
        target_file = db_dir / filename
        if target_file.exists():
            return target_file

        # Fallback to legacy path if target doesn't exist yet
        legacy_file = base / filename
        if legacy_file.exists():
            return legacy_file

        # Default to new target path inside db/
        db_dir.mkdir(parents=True, exist_ok=True)
        return target_file

    legacy_file = base / filename
    if legacy_file.exists():
        return legacy_file
    return get_data_dir() / filename




def migrate_databases_to_db_folder() -> None:
    """Migrate database/cache files from legacy root location into db/ directory on boot.
    Handles legacy migration, size verification, and stale root file cleanup cleanly.
    """
    try:
        base = get_base_path()
        db_dir = base / "db"
        db_dir.mkdir(parents=True, exist_ok=True)

        for filename in DB_RELOCATED_FILES:
            legacy_path = base / filename
            target_path = db_dir / filename

            # Scenario 1: Legacy file exists, target in db/ does NOT exist -> Copy, Verify, Delete Legacy
            if legacy_path.exists() and not target_path.exists():
                try:
                    import shutil

                    shutil.copy2(str(legacy_path), str(target_path))
                    if target_path.exists() and target_path.stat().st_size == legacy_path.stat().st_size:
                        os.remove(str(legacy_path))
                        logger.info(f"[DB Migration] Successfully migrated {filename} -> db/{filename}")
                    else:
                        logger.warning(f"[DB Migration] Size verification failed for {filename} — preserving legacy file")
                except Exception as move_err:
                    logger.warning(f"[DB Migration] Could not copy {filename} to db/: {move_err}")

            # Scenario 2: BOTH legacy file and db/ target file exist (reconciliation)
            elif legacy_path.exists() and target_path.exists():
                try:
                    legacy_size = legacy_path.stat().st_size
                    target_size = target_path.stat().st_size

                    # If file in db/ is larger or valid, db/ file is authoritative -> remove stale root file
                    if target_size >= legacy_size and target_size > 0:
                        os.remove(str(legacy_path))
                        logger.info(f"[DB Migration] Cleaned up stale root file {filename} (authoritative db/{filename} kept)")
                    elif legacy_size > target_size and legacy_size > 0:
                        import shutil
                        shutil.copy2(str(legacy_path), str(target_path))
                        os.remove(str(legacy_path))
                        logger.info(f"[DB Migration] Updated db/{filename} with fuller legacy file and cleaned root copy")
                except Exception as clean_err:
                    logger.warning(f"[DB Migration] Could not reconcile duplicate {filename}: {clean_err}")

    except Exception as e:
        logger.warning(f"[DB Migration] Database migration check failed: {e}")




def is_running_in_pyinstaller() -> bool:
    """Check if the application is running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")




def format_bytes(num_bytes: int) -> str:
    """Format byte count into human-readable string (B, KB, MB, GB, TB)."""
    try:
        num = float(num_bytes)
    except (ValueError, TypeError):
        return ""
    if num >= 1024**4:
        return f"{num / (1024**4):.2f} TB"
    elif num >= 1024**3:
        return f"{num / (1024**3):.2f} GB"
    elif num >= 1024**2:
        return f"{num / (1024**2):.1f} MB"
    elif num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{int(num)} B"




def resource_path(relative_path: str) -> Path:
    """Absolute path to a bundled resource, in dev and when frozen."""
    norm = str(relative_path).replace("\\", "/")
    if norm in ("deps/DepotDownloader.dll", "deps/depotdownloader.dll"):
        try:
            from utils.paths import Paths

            return Paths.depot_downloader_dll()
        except Exception:
            pass

    base_path = getattr(sys, "_MEIPASS", None)
    if base_path is None:
        base_path = os.path.dirname(os.path.abspath(sys.argv[0]))
    return Path(os.path.join(base_path, relative_path))

