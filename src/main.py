"""EXE-fingerprinter — the Steam .exe downloader.

One interface only: the terminal. The app runs as a TUI (``paws``-style) and
is the same in ``--api`` mode, where it fingerprints first and offers two
options — download the ``.exe`` files, or use the existing desktop flow.

The previous Qt desktop window was removed; nothing imports PyQt6 anymore.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

from version import APP_VERSION  # noqa: F401  (imported for --version and the build stamp)


def _announce_debug() -> None:
    if os.environ.get("EXEFP_DEBUG") == "1" or "--debug" in sys.argv:
        os.environ.pop("EXEFP_DEBUG", None)


def main() -> None:
    multiprocessing.freeze_support()

    # Make the script importable when run as ``python src/main.py``.
    src_dir = Path(__file__).resolve().parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    debug = os.environ.get("EXEFP_DEBUG") == "1" or "--debug" in sys.argv
    cleaned = [a for a in sys.argv if a != "--debug"]
    if debug:
        os.environ["EXEFP_DEBUG"] = "1"

    from utils.logger import setup_logging

    if debug:
        setup_logging(console=True)
    else:
        setup_logging()

    if "--version" in cleaned or "-V" in cleaned:
        print(f"EXE-fingerprinter {APP_VERSION}")
        return
    if "--help" in cleaned or "-h" in cleaned:
        print(__doc__)
        return

    from tui.app import run_tui

    sys.exit(run_tui(cleaned))


if __name__ == "__main__":
    main()
