"""Native file/folder pickers, with graceful TUI fallback.

Linux/BSD uses zenity or kdialog when present, macOS/Windows fall back to
tkinter's askdirectory / askopenfilename. When nothing is available the
caller falls back to a plain path Input in the TUI.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def _run(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[NativePicker] %s failed: %s", cmd[0], exc)
        return None
    if result.returncode != 0:
        return None
    path = (result.stdout or "").strip()
    return path or None


def _tk_pick_folder(title: str = "Choose a destination") -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        result = filedialog.askdirectory(title=title, mustexist=False)
        root.destroy()
        return result or None
    except Exception as exc:
        logger.debug("[NativePicker] tkinter folder pick failed: %s", exc)
        return None


def _tk_pick_file(title: str = "Choose a file", filetypes=(("Executables", "*.exe"), ("All files", "*.*"))) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        result = filedialog.askopenfilename(title=title, filetypes=list(filetypes))
        root.destroy()
        return result or None
    except Exception as exc:
        logger.debug("[NativePicker] tkinter file pick failed: %s", exc)
        return None


def pick_folder(title: str = "Choose a destination") -> str | None:
    # one tool per platform, in preference order: a cancel is a cancel, never a second dialog
    if sys.platform.startswith(("linux", "freebsd", "openbsd")):
        for cmd in (
            ["zenity", "--file-selection", "--directory", f"--title={title}"],
            ["kdialog", "--getexistingdirectory", os.path.expanduser("~"), "--title", title],
            ["qarma", "--file-selection", "--directory", f"--title={title}"],
        ):
            if shutil.which(cmd[0]):
                return _run(cmd)
    if sys.platform in ("win32", "darwin") or _tk_available():
        return _tk_pick_folder(title)
    return None


def pick_executable(title: str = "Choose a file to fingerprint") -> str | None:
    # one tool per platform, in preference order: a cancel is a cancel, never a second dialog
    if sys.platform.startswith(("linux", "freebsd", "openbsd")):
        for cmd in (
            ["zenity", "--file-selection", f"--title={title}", "--file-filter=executables|*.exe|*.EXE", "--file-filter=*"],
            ["kdialog", "--getopenfilename", os.path.expanduser("~"), "executables (*.exe)", "--title", title],
        ):
            if shutil.which(cmd[0]):
                return _run(cmd)
    if sys.platform in ("win32", "darwin") or _tk_available():
        return _tk_pick_file(title)
    return None


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:
        return False


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy across platforms."""
    if sys.platform == "win32":
        try:
            subprocess.run("clip", input=text, text=True, check=True, timeout=10)
            return True
        except Exception as exc:
            logger.debug("[Clipboard] clip failed: %s", exc)
            return False

    candidates = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates += [["wl-copy"]]
    if os.environ.get("DISPLAY"):
        candidates += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]

    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, timeout=10)
                return True
            except Exception as exc:
                logger.debug("[Clipboard] %s failed: %s", cmd[0], exc)
    return False
