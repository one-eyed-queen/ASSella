"""
exe_scan_runner.py — Run `-manifest-only` dumps for a game's depots and
collect every Windows executable found.

Used by the EXE-fingerprinter actions in the depot-selection dialogs.
Runs off the GUI thread; emits progress so the caller can drive a progress
dialog, and a finished signal carrying the flattened exe inventory.
"""

import os
import subprocess
import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ui.dialogs.depot_selection.manifest_dump import (
    ManifestDumpError,
    prepare_manifest_dump,
)
from core import exe_scan

logger = logging.getLogger(__name__)


class ExeScanThread(QThread):
    """Scan each depot for .exe files, sequentially."""

    progress = pyqtSignal(str, int)       # (message, index)
    finished_scan = pyqtSignal(list, list)  # (entries, errors)

    def __init__(self, app_id, depot_ids, parent=None):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.depot_ids = [str(d) for d in depot_ids]
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from utils.helpers import get_dotnet_path, resource_path

        entries = []
        errors = []

        dotnet_path = get_dotnet_path()
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        for index, depot_id in enumerate(self.depot_ids):
            if self._cancelled:
                errors.append(f"Scan cancelled before depot {depot_id}.")
                break

            total = len(self.depot_ids)
            self.progress.emit(
                f"Preparing manifest for depot {depot_id} ({index + 1}/{total})…", index
            )
            try:
                dump = prepare_manifest_dump(self.app_id, depot_id)
            except ManifestDumpError as e:
                errors.append(f"Depot {depot_id}: {e}")
                continue
            except Exception as e:
                logger.exception("[ExeScan] Unexpected error preparing depot %s", depot_id)
                errors.append(f"Depot {depot_id}: unexpected error: {e}")
                continue

            # Use a per-depot keys file: the shared depot.keys in the temp dir
            # would be overwritten by the next depot's prepare_manifest_dump,
            # which would break this depot's -depotkeys.
            per_depot_keys = os.path.join(dump.temp_dir, f"keys_{depot_id}.vdf")
            try:
                with open(dump.keys_path, "r", encoding="utf-8") as src:
                    key_line = src.read()
                with open(per_depot_keys, "w", encoding="utf-8") as dst:
                    dst.write(key_line)
                dump = dump.__class__(
                    app_id=dump.app_id,
                    depot_id=dump.depot_id,
                    manifest_id=dump.manifest_id,
                    manifest_file=dump.manifest_file,
                    keys_path=per_depot_keys,
                    temp_dir=dump.temp_dir,
                    txt_path=dump.txt_path,
                )
            except OSError as e:
                logger.debug(f"[ExeScan] Could not write per-depot keys for {depot_id}: {e}")
                # Fall back to the shared keys file (single-depot scans are unaffected).

            cmd = dump.command(dotnet_path, dll_path)
            self.progress.emit(
                f"Fetching manifest for depot {depot_id} from Steam…", index
            )
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if result.returncode != 0:
                    errors.append(f"Depot {depot_id}: DepotDownloader exited with code {result.returncode}.")
                    continue
            except Exception as e:
                errors.append(f"Depot {depot_id}: failed to run DepotDownloader: {e}")
                continue

            if not os.path.exists(dump.txt_path):
                errors.append(f"Depot {depot_id}: manifest listing was not produced.")
                continue

            self.progress.emit(f"Scanning executables in depot {depot_id}…", index)
            found = exe_scan.list_executables(dump.txt_path)
            for entry in found:
                entries.append({
                    "depot_id": depot_id,
                    "path": entry["path"],
                    "size": entry["size"],
                })
            self.progress.emit(
                f"Depot {depot_id}: {len(found)} executable(s) found.", index + 1
            )

        entries.sort(key=lambda e: (e["depot_id"], e["path"].lower()))
        self.finished_scan.emit(entries, errors)
