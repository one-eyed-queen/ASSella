"""EXE-fingerprinter TUI (Textual).

Terminal-native interface: search → fingerprint → download just the
executables. No Qt, no alternate screen hand-rolled by us — Textual owns the
rendering, so this works in any terminal that Textual supports (kitty,
foot, wezterm, Windows Terminal, plain xterm).

``--api --appid ID [--download]`` is the non-interactive path for scripts and
CI and does not need a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from textual.app import App
from textual.binding import Binding

from tui import theme as theme_mod
from tui.screens import GameScreen, SearchScreen, SettingsScreen

CSS_PATH = Path(__file__).with_name("styles.tcss")


class FingerprinterApp(App):
    TITLE = "EXE-fingerprinter"
    SUB_TITLE = ".exe downloader"
    CSS_PATH = str(CSS_PATH)
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit", show=False),
        Binding("f2", "settings", "settings", show=False),
    ]

    def __init__(self, argv: Optional[list] = None):
        super().__init__()
        self.argv = list(argv if argv is not None else sys.argv)
        self.api_mode = "--api" in self.argv or "--api-mode" in self.argv
        for theme in theme_mod.themes():
            self.register_theme(theme)
        self.theme = theme_mod.resolve_theme_name()
        if self.theme not in {t.name for t in theme_mod.themes()}:
            # The stored name was from the old palette; fall back to the default pair.
            self.theme = "colour-blind" if theme_mod.colour_blind_enabled() else "rice"

    def on_mount(self) -> None:
        self.push_screen(SearchScreen())

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())


def run_tui(argv: Optional[list] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    # Non-interactive CI path: fingerprint (and optionally download) without a TTY.
    batch = _api_batch(argv)
    if batch is not None:
        return batch

    try:
        app = FingerprinterApp(argv)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # A rendering failure should not strand the user with a blank screen.
        print(f"EXE-fingerprinter TUI failed to start: {exc}", file=sys.stderr)
        return 1


def _api_batch(argv):
    """Non-interactive ``--api --appid …`` path for scripts and CI."""
    if "--api" not in argv or "--appid" not in argv:
        return None

    import json

    def console_print(msg: str) -> None:
        print(msg)

    appid = None
    for index, arg in enumerate(argv):
        if arg == "--appid" and index + 1 < len(argv):
            appid = argv[index + 1]
        elif arg.startswith("--appid="):
            appid = arg.split("=", 1)[1]
    if not appid or not str(appid).isdigit():
        return None

    download = "--download" in argv

    from core.steam_api import fetch_steamcmd_info
    from tui import engine

    info = fetch_steamcmd_info(str(appid)) or {}
    app_name = info.get("name") or f"App {appid}"
    console_print(f"Fingerprinting {appid} — {app_name}")

    scan = engine.scan_app(str(appid))
    if not scan.entries:
        console_print("No executables found.")
        for error in scan.errors:
            console_print(f"  error: {error}")
        engine.cleanup_keys(scan)
        return 2

    console_print(f"Found {len(scan.entries)} executable(s):")
    for entry in scan.entries:
        console_print(f"  {entry['depot_id']:>10}  {entry['path']}")

    if not download:
        return 0

    console_print(f"Downloading to {engine.default_destination(str(appid))}")
    count, errors = engine.download_selected(
        scan,
        scan.entries,
        destination=engine.default_destination(str(appid)),
        progress=lambda line: console_print(f"  {line}"),
    )
    engine.cleanup_keys(scan)
    if errors:
        for error in errors:
            console_print(f"  error: {error}")
        return 2
    console_print(f"Done — {count} depot(s) downloaded.")
    return 0
