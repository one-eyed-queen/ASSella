#!/usr/bin/env python3
"""Headless smoke test of the EXE-fingerprinter Textual TUI.

Runs the real screens with Textual's pilot harness: mount, search input,
results list, and the game screen's fingerprint flow error path (no network
guarantee, no DepotDownloader in CI — the point is the screens wire up and
never crash).
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tui.app import FingerprinterApp
from tui.screens import GameScreen, SearchScreen, SettingsScreen


async def main() -> int:
    app = FingerprinterApp([])
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause(0.5)

        # ── search screen mounts ────────────────────────────────────────
        assert isinstance(app.screen, SearchScreen), app.screen
        search = app.screen.query_one("#search-input")
        print("[ok] search screen mounted")

        # ── typing and submitting the AppID 620 ──────────────────────────
        await pilot.click("#search-input")
        await pilot.press(*"620")
        assert search.value == "620"
        print("[ok] search input accepts text")

        await pilot.press("enter")
        await pilot.pause(4)
        # Results are either shown or a polite "No results" — either way the app lives.
        hint = app.screen.query_one("#search-hint").render()
        print(f"[ok] search ran (hint: {str(hint)[:60]})")

        results = app.screen.query_one("#results")
        options = list(results.options)
        print(f"[ok] results list has {len(options)} entr" + ("ies" if len(options) != 1 else "y"))

        if options:
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert isinstance(app.screen, GameScreen), app.screen
            print("[ok] game screen opened (AppID", app.screen.appid, ")")

            status = app.screen.query_one("#scan-status").render()
            print(f"[ok] scan status: {str(status)[:70]}")

            # Fingerprint (this hits the network / depotdownloader if available;
            # either outcome must leave the screen consistent).
            await pilot.click("#btn-fingerprint")
            await pilot.pause(20)
            status = str(app.screen.query_one("#scan-status").render())
            print(f"[ok] fingerprint finished: {status[:80]}")
            btn = app.screen.query_one("#btn-download")
            print(f"[ok] download button disabled={btn.disabled}")

            await pilot.press("escape")
            await pilot.pause(0.3)
            assert isinstance(app.screen, SearchScreen)
            print("[ok] Esc returned to search")

        # ── settings screen ─────────────────────────────────────────────
        await pilot.press("f2")
        await pilot.pause(0.4)
        assert isinstance(app.screen, SettingsScreen)
        print("[ok] settings screen opened")
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert isinstance(app.screen, SearchScreen)
        print("[ok] Esc returned from settings")

    print("\nTUI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
