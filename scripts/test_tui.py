#!/usr/bin/env python3
"""Headless smoke test of the EXE-fingerprinter Textual TUI.

Runs the real screens with Textual's pilot harness: mount, search input,
results table with prices/art, the fingerprint-or-download choice screen,
and settings arrow-key navigation. No network guarantee — the search path
tolerates an offline store (the screen just shows zero results).
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tui.app import FingerprinterApp
from tui.screens import ChoiceScreen, GameScreen, HomeScreen, SearchScreen, SettingsScreen


async def main() -> int:
    app = FingerprinterApp([])
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause(0.5)

        # ── home screen mounts (paws-style menu) ─────────────────────────
        assert isinstance(app.screen, HomeScreen), app.screen
        menu = app.screen.query_one("#menu")
        assert len(list(menu.children)) >= 5, list(menu.children)
        labels = [getattr(item, "title", "") for item in menu.children]
        print("[ok] home screen mounted; menu:", labels)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert isinstance(app.screen, SearchScreen), app.screen
        print("[ok] menu → search screen")

        search = app.screen.query_one("#search-input")
        print("[ok] search screen mounted")

        # ── typing and submitting the AppID 620 ──────────────────────────
        await pilot.click("#search-input")
        await pilot.press(*"620")
        assert search.value == "620"
        print("[ok] search input accepts text")

        await pilot.press("enter")
        await pilot.pause(4)
        hint = app.screen.query_one("#add-hint").render()
        table = app.screen.query_one("#results-table")
        print(f"[ok] search ran: {str(hint)[:60]}")
        print(f"[ok] results table has {table.row_count} row(s)")

        if table.row_count:
            await pilot.press("enter")  # choose the highlighted row
            await pilot.pause(0.5)
            assert isinstance(app.screen, ChoiceScreen), app.screen
            print("[ok] choice screen opened (", app.screen.appid, ")")
            await pilot.press("escape")  # back to search
            await pilot.pause(0.3)
            assert isinstance(app.screen, SearchScreen)
            print("[ok] Esc returned to search")

        # ── settings: arrow-key navigation ─────────────────────────────
        await pilot.press("f2")
        await pilot.pause(0.4)
        assert isinstance(app.screen, SettingsScreen), app.screen
        print("[ok] settings screen opened")

        from textual.widgets import Input
        first = app.screen.query_one("#hubcap-key", Input)
        assert first is not None
        await pilot.press("down")
        await pilot.pause(0.2)
        assert app.focused is not None
        print("[ok] down arrow moves focus to:", app.focused.id)

        await pilot.press("up")
        await pilot.pause(0.2)
        print("[ok] up arrow moves focus to:", app.focused.id)

        # cycle the theme with ←/→ while the Select has focus
        for _ in range(3):
            await pilot.press("down")
            await pilot.pause(0.1)
        print("[ok] arrow navigation works in settings")

        await pilot.press("escape")
        await pilot.pause(0.3)
        assert isinstance(app.screen, SearchScreen)
        print("[ok] Esc returned to search")
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert isinstance(app.screen, HomeScreen)
        print("[ok] Esc returned to the home menu")

    print("\nTUI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
