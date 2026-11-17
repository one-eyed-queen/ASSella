"""Paths for EXE-fingerprinter's bundled assets (paws layout)."""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

def paws_home() -> Path:
    """The app's own folder (settings, backups). Mirrors paws' layout."""
    import os
    import sys

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "EXE-fingerprinter"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "EXE-fingerprinter"
