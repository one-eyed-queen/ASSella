"""Settings shim in the shape paws' TUI expects.

paws keeps its display settings in its own JSON (``minimal``, ``icons``,
``colorblind``). We store ours in the shared JSON at
``~/.config/EXE-fingerprinter/settings.json`` and only adapt the names.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}


def _bool(raw, default=False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUE


def _store():
    from utils.settings import get_settings

    return get_settings()


def is_minimal() -> bool:
    raw = str(_store().value("tui_theme", "auto") or "auto").strip().lower()
    if raw == "minimal":
        return True
    if raw in ("", "auto"):
        return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM", "").lower() == "dumb"
    return False


def _colorblind_raw() -> str:
    value = str(_store().value("colorblind", "") or "").strip().lower()
    if not value:
        # backward compat with the old colour_blind_mode boolean
        if _bool(_store().value("colour_blind_mode", False), False):
            return "deuteranopia"
        return "off"
    return value


def get_setting(key: str, default=None):
    if key == "minimal":
        return "1" if is_minimal() else "0"
    if key == "icons":
        return str(_store().value("icons", "auto") or "auto")
    if key == "colorblind":
        return _colorblind_raw()
    return _store().value(key, default)


def set_setting(key: str, value) -> None:
    settings = _store()
    settings.setValue(key, value)
    settings.sync()


def colorblind_enabled() -> bool:
    return _colorblind_raw() not in ("", "off")


def set_colorblind(mode: str) -> None:
    set_setting("colorblind", "off" if mode == "off" else mode)
    set_setting("colour_blind_mode", mode != "off")


def colorblind_mode() -> str:
    return _colorblind_raw()
