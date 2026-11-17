"""Raw terminal input for the TUI.

POSIX uses ``termios`` + ``select`` so redraws can happen on a timeout while
waiting for a key. Windows uses ``msvcrt`` with a short sleep, which is enough
for a TUI that redraws a few times per second.
"""

from __future__ import annotations

import os
import select
import sys

try:  # POSIX
    import termios
    import tty
except ImportError:  # Windows
    termios = None
    tty = None


_KEYS = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[5~": "pageup",
    "\x1b[6~": "pagedown",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[Z": "tab_prev",
    "\r": "enter",
    "\n": "enter",
    "\t": "tab",
    "\x7f": "backspace",
    "\x08": "backspace",
    "\x03": "ctrl_c",
    "\x04": "ctrl_d",
    "\x1b": "esc",
    " ": "space",
}


class RawTerminal:
    """Context manager that puts the tty in raw mode and restores it."""

    def __init__(self):
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self._saved = None

    def __enter__(self):
        if self.fd is not None and termios is not None:
            self._saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None and termios is not None and self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
        return False


def _read_posix(timeout: float) -> str:
    fd = sys.stdin.fileno()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return ""
    first = os.read(fd, 1)
    if not first:
        return "ctrl_d"
    if first == b"\x1b":
        # Collect the rest of a CSI sequence, if one is waiting.
        seq = first
        while len(seq) < 6:
            more, _, _ = select.select([fd], [], [], 0.01)
            if not more:
                break
            seq += os.read(fd, 1)
            if seq[-1:] in b"~ABCDHFZ":
                break
        text = seq.decode("utf-8", errors="ignore")
        return _KEYS.get(text, "esc")
    try:
        text = first.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return _KEYS.get(text, text)


def _read_windows(timeout: float) -> str:
    import msvcrt
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if msvcrt.kbhit():
            first = msvcrt.getwch()
            if first in ("\x00", "\xe0"):
                second = msvcrt.getwch()
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(second, "")
            return _KEYS.get(first, first)
        time.sleep(0.02)
    return ""


def read_key(timeout: float = 0.15) -> str:
    """Return a key token (``up``, ``enter``, ``a``, …) or '' on timeout."""
    if os.name == "nt":
        return _read_windows(timeout)
    return _read_posix(timeout)


def terminal_size(default=(100, 32)):
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return default
