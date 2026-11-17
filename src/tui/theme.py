"""Colour palettes for the EXE-fingerprinter TUI.

* ``rice``    — warmer and higher contrast, the default on an interactive terminal,
* ``minimal`` — greyscale, for terminals with limited colour,
* auto picks rice (or minimal when NO_COLOR/TERM=dumb), and ``colour_blind``
  swaps red/green signalling for a blue/orange pair that stays distinguishable
  with deuteranopia and protanopia.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    panel: str
    border: str
    text: str
    muted: str
    accent: str
    accent_alt: str
    success: str
    warning: str
    error: str
    selection: str


RICE = Theme(
    name="rice",
    background="#0d0b09",
    panel="#15110d",
    border="#3a2f22",
    text="#f0e6d2",
    muted="#a08c74",
    accent="#ffb86b",
    accent_alt="#ff8fb3",
    success="#a8d08d",
    warning="#ffd479",
    error="#ff6b6b",
    selection="#2c2116",
)

MINIMAL = Theme(
    name="minimal",
    background="black",
    panel="#141414",
    border="#3a3a3a",
    text="#e0e0e0",
    muted="#8a8a8a",
    accent="#d0d0d0",
    accent_alt="#b0b0b0",
    success="#d0d0d0",
    warning="#c0c0c0",
    error="#f0f0f0",
    selection="#2a2a2a",
)

COLOUR_BLIND = Theme(
    name="colour-blind",
    background="#0e1116",
    panel="#161b22",
    border="#30363d",
    text="#f0f6fc",
    muted="#9aa4b2",
    accent="#4ea1ff",
    accent_alt="#e69f00",
    success="#56b4e9",   # blue, not green
    warning="#e69f00",   # orange
    error="#d55e00",     # vermillion
    selection="#1b2a3d",
)


def _setting(key: str, default):
    """Read a GUI setting without requiring a QApplication."""
    try:
        from utils.settings import get_settings

        return get_settings().value(key, default)
    except Exception:
        return default


def colour_blind_enabled() -> bool:
    raw = _setting("colour_blind_mode", False)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def resolve_theme(name: str | None = None) -> Theme:
    """Pick a palette from a name, the settings, or the environment.

    ``auto`` (and ``None``) chooses minimal when the terminal asks for no
    colour, otherwise rice.
    """
    requested = (name or _setting("tui_theme", "auto") or "auto").strip().lower()
    if requested in ("ocean", "default", ""):
        requested = "rice"

    if requested == "auto":
        if os.environ.get("NO_COLOR") or os.environ.get("TERM", "").lower() == "dumb":
            requested = "minimal"
        else:
            requested = "rice"

    if colour_blind_enabled():
        base = COLOUR_BLIND
        # Keep the requested background variant but take the signalling colours.
        if requested == "minimal":
            base = Theme(**{**COLOUR_BLIND.__dict__, "name": "colour-blind-minimal"})
        return base

    return {"rice": RICE, "minimal": MINIMAL}.get(requested, RICE)
