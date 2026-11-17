"""
exe_scan.py — Find and select Windows executables (.exe) inside a depot manifest dump.

The EXE-fingerprinter feature uses the same `-manifest-only` text dumps the
"Browse Files..." selector uses. This module turns those dumps into a clean
list of executable files so the UI can present one checkbox per .exe — every
one pre-checked — and hand the result to DepotDownloader's `-filelist` filter.
"""

import os
import logging

logger = logging.getLogger(__name__)

EXE_EXTENSION = ".exe"


def parse_manifest_dump_files(txt_path: str):
    """Parse a `-manifest-only` dump into [{path, size}, ...].

    Returns an empty list when the dump does not exist. Skips directory
    entries and deduplicates by path.
    """
    files = []
    seen = set()

    if not txt_path or not os.path.exists(txt_path):
        return files

    start_parsing = False
    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if "Size Chunks File SHA" in line_stripped:
                start_parsing = True
                continue
            if not start_parsing:
                continue

            parts = line_stripped.split(None, 4)
            if len(parts) < 5:
                continue
            try:
                size = int(parts[0])
                chunks = int(parts[1])
                name = parts[4].strip()
            except (ValueError, IndexError):
                continue

            if not name or name in seen:
                continue
            # Directory sentinel rows: size 0, no chunks. Skip them.
            if size == 0 and chunks == 0:
                continue
            files.append({"path": name, "size": size})
            seen.add(name)

    return files


def list_executables(txt_path: str):
    """Return only the .exe entries of a manifest dump, sorted by path (case-insensitive)."""
    files = parse_manifest_dump_files(txt_path)
    exes = [f for f in files if f["path"].lower().endswith(EXE_EXTENSION)]
    exes.sort(key=lambda f: f["path"].lower())
    return exes


def list_executables_for_depots(depot_to_dump_path: dict):
    """Scan several dumped manifests and return [{depot_id, path, size}, ...]."""
    flattened = []
    for depot_id, txt_path in depot_to_dump_path.items():
        for entry in list_executables(txt_path):
            flattened.append({
                "depot_id": str(depot_id),
                "path": entry["path"],
                "size": entry["size"],
            })
    flattened.sort(key=lambda f: (f["depot_id"], f["path"].lower()))
    return flattened


def build_exe_filelist(entries):
    """Write the DepotDownloader -filelist text for a list of {path} dicts."""
    return "\n".join(e["path"].replace("\\", "/") for e in entries) + "\n"


def is_game_executable(path: str) -> bool:
    """True when a manifest path points at a Windows executable."""
    return bool(path) and path.lower().endswith(EXE_EXTENSION)


def common_root(entries):
    """Shortest common directory prefix across all .exe paths (e.g. 'game/bin/').

    May be useful for callers that want to know where the game's executables
    live; returns '' when there is no common directory.
    """
    dirs = []
    for e in entries:
        parts = e["path"].replace("\\", "/").split("/")[:-1]
        dirs.append(parts)
    if not dirs:
        return ""
    prefix = dirs[0]
    for parts in dirs[1:]:
        common = []
        for a, b in zip(prefix, parts):
            if a.lower() != b.lower():
                break
            common.append(a)
        prefix = common
        if not prefix:
            return ""
    return "/".join(prefix) + "/" if prefix else ""
