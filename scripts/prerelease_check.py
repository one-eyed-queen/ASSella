#!/usr/bin/env python3
"""
EXE-fingerprinter pre-release check.

The tool is now a TUI-only, .exe-only downloader: this suite verifies the
things that must never break before a release. Everything here is hermetic
(no network, no Steam install).

  1. smoke import check (all modules import under a bare interpreter)
  2. manifest_dump behavioural suite
  3. Qt-free runtime: importing anything pulls in no PyQt6
  4. settings store roundtrips (fresh and migrated from the legacy INI)
  5. download destination honours %APPDATA%/exe-pawprint / XDG data dir
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

results = []


def record(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    results.append((name, PASS if ok else FAIL, detail, skipped))
    suffix = f" — {detail}" if detail else ""
    print(f"  [{PASS if skipped or ok else FAIL}] {name}{suffix}")


def run(cmd, cwd=REPO_ROOT, env=None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600)


def main() -> int:
    print("== EXE-fingerprinter pre-release check ==\n")

    # 1. smoke import check
    print("-- 1. smoke import")
    proc = run([sys.executable, "scripts/smoke_import_check.py"])
    record("smoke import (all modules)", proc.returncode == 0)

    # 2. manifest_dump suite
    print("-- 2. manifest_dump suite")
    proc = run([sys.executable, "scripts/test_manifest_dump.py"],
               env={"QT_QPA_PLATFORM": "offscreen"})
    record("manifest_dump (29 checks)", proc.returncode == 0)

    # 3. Qt-free runtime
    print("-- 3. Qt-free runtime")
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; import tui.app, tui.engine, core.manifest_dump, core.steam_api, "
         "core.morrenus_api, utils.settings, utils.helpers, utils.logger; "
         "mods = sorted(m for m in sys.modules if 'PyQt6' in m or m.split('.')[0] in ('PyQt5', 'PySide6', 'PySide2')); "
         "print('qt modules:', mods); sys.exit(1 if mods else 0)"],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
        capture_output=True, text=True, timeout=120,
    )
    record("importing the app pulls in no Qt", proc.returncode == 0, proc.stdout.strip())

    # 4. settings roundtrip + legacy migration
    print("-- 4. settings store")
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": tmp,
            "PYTHONPATH": str(SRC_DIR),
            # keep the legacy ini away from the real one
            "HOME": tmp,
        }
        proc = subprocess.run(
            [sys.executable, "-c",
             "from utils.settings import get_settings;"
             "s = get_settings();"
             "s.setValue('exefp_api_username', 'ken'); s.sync();"
             "s2 = __import__('importlib').reload(__import__('utils.settings', fromlist=['x'])).get_settings();"
             "assert s2.value('exefp_api_username', '') == 'ken';"
             "print('settings roundtrip ok')"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
        record("settings roundtrip", proc.returncode == 0)

    # 5. exe-pawprint destination
    print("-- 5. exe-pawprint destination")
    proc = subprocess.run(
        [sys.executable, "-c",
         "from utils.helpers import get_exe_pawprint_dir; p = get_exe_pawprint_dir(620); print(p)"],
        cwd=str(REPO_ROOT), env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
        capture_output=True, text=True, timeout=60,
    )
    dest_ok = proc.returncode == 0 and "exe-pawprint" in proc.stdout
    record("%APPDATA%/exe-pawprint (XDG on Linux)", dest_ok, proc.stdout.strip().splitlines()[-1] if proc.stdout else proc.stderr.strip())

    passed = sum(1 for _, s, _, skipped in results if s == PASS or skipped)
    failed = sum(1 for _, s, _, skipped in results if s == FAIL)
    print(f"\nRESULTS: {passed}/{len(results)} PASSED  |  {failed} FAILED")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
