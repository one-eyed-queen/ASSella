"""Textual themes for EXE-fingerprinter.

Colour palette ported from paws (its owner's project): near-black navy with
blue/cyan accents. Three variants ship:

* ``rice``    — the paws palette; default on any terminal with truecolor.
* ``minimal`` — greyscale; automatically picked on dumb / NO_COLOR terminals.
* colour-blind swaps red/green signalling for a blue/orange pair.
"""

from __future__ import annotations

import os

from textual.theme import Theme

RICE = Theme(
    name="rice",
    primary="#4066ff",
    secondary="#9b4dff",
    accent="#00d0ff",
    warning="#ffb84d",
    error="#ff4da6",
    success="#3ddc97",
    foreground="#dbe4ff",
    background="#0a1020",
    surface="#101a30",
    panel="#0c1424",
    boost="#1a2547",
    dark=True,
)

MINIMAL = Theme(
    name="minimal",
    primary="#e8e8e8",
    secondary="#a0a0a0",
    accent="#ffffff",
    warning="#e0e0e0",
    error="#ffffff",
    success="#d0d0d0",
    foreground="#d8d8d8",
    background="#101010",
    surface="#161616",
    panel="#161616",
    boost="#202020",
    dark=True,
)

# Blue/orange signalling pair that survives deuteranopia and protanopia,
# on the same navy background as the rice palette.
COLOUR_BLIND = Theme(
    name="colour-blind",
    primary="#6cb4ff",
    secondary="#ff9d5c",
    accent="#9cd0ff",
    warning="#ffb84d",
    error="#ff9d5c",
    success="#6cb4ff",
    foreground="#dbe4ff",
    background="#0a1020",
    surface="#101a30",
    panel="#0c1424",
    boost="#1a2547",
    dark=True,
)


def _setting(key: str, default=None):
    from utils.settings import get_settings

    return get_settings().value(key, default)


def colour_blind_enabled() -> bool:
    raw = _setting("colour_blind_mode", False)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def resolve_theme_name(name: str | None = None) -> str:
    requested = (name or str(_setting("tui_theme", "auto") or "auto")).strip().lower()
    if requested in ("", "default", "ocean"):
        requested = "rice"
    if requested == "auto":
        if os.environ.get("NO_COLOR") or os.environ.get("TERM", "").lower() == "dumb":
            requested = "minimal"
        else:
            requested = "rice"
    return requested


def themes() -> list[Theme]:
    if colour_blind_enabled():
        return [COLOUR_BLIND, MINIMAL]
    return [RICE, MINIMAL]
