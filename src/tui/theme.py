"""Textual themes for EXE-fingerprinter.

* ``rice``    — the default on any terminal with truecolor, matches the
  desktop's near-black canvas with warm orange accents.
* ``minimal`` — greyscale; automatically picked on dumb / NO_COLOR terminals.

Colour-blind mode replaces red/green signalling with blue/orange. Textual
handles the actual terminal details (alternate screen, cursor, repaints) so
this module only ships palette definitions and the detection helpers.
"""

from __future__ import annotations

import os

from textual.theme import Theme

RICE = Theme(
    name="rice",
    primary="#ffb86b",
    secondary="#ff8fb3",
    accent="#ffd76b",
    warning="#ffd76b",
    error="#ff8f8f",
    success="#a8d08d",
    foreground="#f0e6d2",
    background="#0d0b09",
    surface="#15110d",
    panel="#15110d",
    boost="#241f18",
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

# Blue/orange signalling pair that survives deuteranopia and protanopia.
COLOUR_BLIND = Theme(
    name="colour-blind",
    primary="#6cb4ff",
    secondary="#ff9d5c",
    accent="#9cd0ff",
    warning="#ffb84d",
    error="#ff9d5c",
    success="#6cb4ff",
    foreground="#f0e6d2",
    background="#0d0b09",
    surface="#15110d",
    panel="#15110d",
    boost="#241f18",
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
