"""Textual screens for EXE-fingerprinter.

Three screens:

* ``SearchScreen``  — type an AppID or a few letters of a name; results merged
  from Steam Store, SteamDB, Hubcap and the private API.
* ``GameScreen``    — fingerprint the base game's Windows depots (manifest-only)
  and download the selected executables. Nothing else.
* ``SettingsScreen``— private API credentials, theme, colour-blind mode.

``--api`` shows the same two main screens under a fingerprint-only header;
no games are ever added or removed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    Select,
    SelectionList,
    Static,
    Switch,
    ListView,
)
from textual.widgets.option_list import Option

from . import engine

logger = logging.getLogger(__name__)


def _setting(key: str, default=None):
    from utils.settings import get_settings

    return get_settings().value(key, default)


def _bool_setting(key: str, default: bool = False) -> bool:
    raw = _setting(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def _header_text(api_mode: bool) -> str:
    return "EXE-fingerprinter · API mode" if api_mode else "EXE-fingerprinter"


def _subtitle_text(api_mode: bool) -> str:
    if api_mode:
        return "Fingerprint-only — no games added or removed. Executables land in %APPDATA%\\exe-pawprint."
    return "Fingerprint a Steam game, then download just its Windows executables."


def _hero_path() -> str:
    return str(Path(__file__).resolve().parent.parent.parent / "assets" / "paws-bg.png")


def _image_widget():
    try:
        from textual_image.widget import Image

        return Image
    except Exception:
        return None


# ── home screen ───────────────────────────────────────────────────────────

class HomeScreen(Screen):
    """Menu-driven landing page — the paws-style entry screen."""

    BINDINGS = [
        ("ctrl+q", "quit", "quit"),
        ("/", "search", "search"),
        ("f2", "settings", "settings"),
    ]

    MENU = [
        ("search", "Search & fingerprint"),
        ("settings", "Settings (API keys · theme)"),
        ("about", "About"),
        ("quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("EXE-fingerprinter", id="app-header")
        yield Static("Fingerprint a Steam game, then download just its .exe files.", id="app-subtitle")
        with Horizontal(id="home-body"):
            menu = OptionList(id="home-menu")
            yield menu
            hero = _image_widget()
            if hero is not None:
                yield hero(_hero_path(), id="hero")
        yield Static("Enter open · / search · f2 settings · ctrl+q quit", id="footer")

    def on_mount(self) -> None:
        menu = self.query_one("#home-menu", OptionList)
        menu.add_options([Option(label) for _key, label in self.MENU])
        menu.highlighted = 0
        menu.focus()

    def _open(self, index: int) -> None:
        key = self.MENU[index][0]
        if key == "search":
            self.app.push_screen(SearchScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        elif key == "about":
            self.app.push_screen(AboutScreen())
        elif key == "quit":
            self.app.exit()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._open(event.option_index)

    def action_search(self) -> None:
        self.app.push_screen(SearchScreen())

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def action_home(self) -> None:
        self.app.pop_screen()


class AboutScreen(Screen):
    BINDINGS = [("escape", "back", "back")]

    def compose(self) -> ComposeResult:
        yield Static("About", id="app-header")
        yield Static(
            "EXE-fingerprinter 3.0.0\n\n"
            "A TUI that fingerprints a Steam game's base-game Windows depots\n"
            "and downloads just the executable files — nothing else.\n\n"
            "Depot keys come from your local key database, the private API,\n"
            "or Hubcap. Search merges Steam Store, SteamDB, Hubcap and the API.\n\n"
            "SFW assets only. Unofficial — not affiliated with Valve or Steam.\n\n"
            "Esc back",
            id="about-text",
        )
        yield Static("Esc back", id="footer")

    def action_back(self) -> None:
        self.app.pop_screen()


# ── search screen ─────────────────────────────────────────────────────────

class SearchScreen(Screen):
    BINDINGS = [
        ("ctrl+q", "quit", "quit"),
        ("f2", "settings", "settings"),
        ("/", "focus_search", "search"),
        ("escape", "home", "home"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(_header_text(self._is_api_mode()), id="app-header")
        yield Static(_subtitle_text(self._is_api_mode()), id="app-subtitle")
        yield Input(placeholder="AppID or game name…", id="search-input")
        yield Static("Search by AppID or name — Enter searches, ↑↓ moves, Enter opens.", id="search-hint")
        yield OptionList(id="results")
        yield Static("Enter open · ↑↓ move · f2 settings · ctrl+q quit", id="footer")

    def _is_api_mode(self) -> bool:
        return "--api" in self.app.argv or "--api-mode" in self.app.argv

    def on_mount(self) -> None:
        from textual.widgets import Input, OptionList

        self.query_one("#search-input", Input).value = ""
        self.query_one("#search-input", Input).focus()
        self._results: List[Dict[str, Any]] = []

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def action_home(self) -> None:
        self.app.pop_screen()

    @work(thread=True, exclusive=True, group="search")
    def _search(self, query: str) -> None:
        self.app.call_from_thread(self._set_busy, True)
        try:
            from core.unified_search import search

            results = search(query, limit=30, include_steamdb=True)
        except Exception as exc:
            logger.warning("[Search] failed: %s", exc)
            results = []
        self.app.call_from_thread(self._render_results, query, results)
        self.app.call_from_thread(self._set_busy, False)

    def _set_busy(self, busy: bool) -> None:
        widget = self.query_one("#search-input", Input)
        widget.disabled = busy
        self.query_one("#search-hint", Static).update("Searching…" if busy else self._hint_text())

    def _hint_text(self) -> str:
        n = len(self._results)
        if n:
            return f"{n} result(s) — Enter opens, ↑↓ moves."
        return "Type an AppID or game name, then press Enter."

    def _render_results(self, query: str, results: List[Dict[str, Any]]) -> None:
        self._results = results
        view = self.query_one("#results", OptionList)
        view.clear_options()
        hint = self.query_one("#search-hint", Static)
        if not results:
            hint.update(f"No results for '{query}'.")
            return
        view.add_options(
            [
                Option(
                    f"{entry.get('appid')}  {entry.get('name', '')}  ({', '.join(entry.get('sources') or [])})".rstrip()
                )
                for entry in results
            ]
        )
        view.highlighted = 0
        view.focus()
        hint.update(self._hint_text())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._search(event.value.strip())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = event.option_index
        if 0 <= index < len(self._results):
            entry = self._results[index]
            self.app.push_screen(GameScreen(appid=int(entry["appid"]), name=str(entry.get("name") or "")))


# ── game / fingerprint screen ──────────────────────────────────────────────

class GameScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("ctrl+q", "quit", "quit"),
        ("f", "fingerprint", "fingerprint"),
        ("d", "download", "download"),
        ("a", "toggle_all", "toggle all"),
    ]

    def __init__(self, appid: int, name: str) -> None:
        super().__init__()
        self.appid = int(appid)
        self.app_name = name

    def compose(self) -> ComposeResult:
        yield Static(f"App {self.appid} — {self.app_name or '…'}", id="app-title")
        yield Static(
            "Base game only · DLC skipped · manifest-only scan (downloads no game content)",
            id="app-subtitle",
        )
        yield Static("Press F (or the button) to fingerprint this game.", id="scan-status")
        yield SelectionList(id="exe-list")
        with Horizontal(id="button-row"):
            yield Button("Fingerprint (F)", id="btn-fingerprint", variant="primary")
            yield Button("Download .exes (D)", id="btn-download", variant="success", disabled=True)
        yield Static("F fingerprint · D download · A toggle all · Esc back", id="footer")

    def on_mount(self) -> None:
        self._scan: Optional[engine.ScanResult] = None
        self._entries: List[Dict[str, Any]] = []

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    def action_fingerprint(self) -> None:
        self._start_scan()

    def action_download(self) -> None:
        self._start_download()

    def action_toggle_all(self) -> None:
        self.query_one("#exe-list", SelectionList).toggle_all()

    # ── buttons ───────────────────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fingerprint":
            self._start_scan()
        elif event.button.id == "btn-download":
            self._start_download()

    # ── scanning ─────────────────────────────────────────────────────────
    @work(thread=True, exclusive=True, group="scan")
    def _start_scan(self) -> None:
        self.app.call_from_thread(self._set_busy, True, "Scanning Windows base-game depots…")

        def _progress(message: str, step: int, total: int) -> None:
            self._progress(f"{message} ({step + 1}/{total})")

        try:
            result = engine.scan_app(str(self.appid), progress=_progress)
        except Exception as exc:
            logger.warning("[Scan] failed: %s", exc)
            result = None
        self.app.call_from_thread(self._scan_finished, result)

    def _progress(self, message: str) -> None:
        self.app.call_from_thread(self.query_one("#scan-status", Static).update, message)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.query_one("#btn-fingerprint", Button).disabled = busy
        self.query_one("#btn-download", Button).disabled = busy or not self._entries
        if message:
            self.query_one("#scan-status", Static).update(message)

    def _scan_finished(self, result: Optional[engine.ScanResult]) -> None:
        self._scan = result
        self._entries = result.entries if result else []
        status = self.query_one("#scan-status", Static)
        selection = self.query_one("#exe-list", SelectionList)
        selection.clear_options()
        if result is None:
            status.update("Scan failed — check the log.")
        elif not self._entries:
            detail = " · ".join(result.errors) or "no executables found"
            status.update(f"Nothing found: {detail}")
        else:
            selection.add_options(
                [(entry.get("path", f"depot {entry.get('depot_id')}"), index) for index, entry in enumerate(self._entries)]
            )
            status.update(f"{len(self._entries)} executable(s) — select, then press D.")
        self._set_busy(False)
        self.query_one("#btn-download", Button).disabled = not self._entries

    # ── downloading ───────────────────────────────────────────────────────
    @work(thread=True, exclusive=True, group="download")
    def _start_download(self) -> None:
        if self._scan is None or not self._entries:
            return
        selection = self.query_one("#exe-list", SelectionList)
        picked = [self._entries[i] for i in selection.selected]
        if not picked:
            self.app.call_from_thread(
                self.query_one("#scan-status", Static).update, "No executables selected."
            )
            return
        self.app.call_from_thread(self._set_busy, True, f"Downloading {len(picked)} file(s)…")
        dest = engine.default_destination(str(self.appid))
        count, errors = engine.download_selected(
            self._scan,
            picked,
            destination=dest,
            progress=lambda line: self._progress(line[:140]),
        )
        self.app.call_from_thread(self._download_finished, count, errors, dest)

    def _download_finished(self, count: int, errors: List[str], dest: str) -> None:
        status = self.query_one("#scan-status", Static)
        if errors:
            status.update(f"Done — {count} depot(s) finished, {len(errors)} error(s) (see log).")
        else:
            status.update(f"Done — downloaded to {dest}")
        self._set_busy(False)


# ── settings screen ────────────────────────────────────────────────────────

class SettingsScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("ctrl+q", "quit", "quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="app-header")
        yield Static("API keys & appearance — stored locally on this device only.", id="app-subtitle")
        yield Static("Hubcap API key", classes="settings-label")
        yield Input(
            value=str(_setting("morrenus_api_key", "")),
            placeholder="used to fetch manifest bundles for the fingerprint",
            password=True,
            id="hubcap-key",
            classes="settings-input",
        )
        yield Static("Private API (your service)", classes="settings-label")
        yield Input(
            value=str(_setting("exefp_api_base", "https://exe-fingerprinter.vercel.app")),
            placeholder="API base URL",
            id="api-base",
            classes="settings-input",
        )
        yield Input(
            value=str(_setting("exefp_api_username", "")),
            placeholder="username",
            id="api-user",
            classes="settings-input",
        )
        yield Input(value=str(_setting("exefp_api_password", "")), password=True, placeholder="password", id="api-pass", classes="settings-input")
        with Horizontal():
            yield Button("Sign in", id="btn-signin", variant="primary")
        yield Static(self._account_status(), id="settings-status")
        yield Static("Theme (Auto = rice on colour terminals, minimal otherwise)", classes="settings-label")
        yield Select(
            [("Auto", "auto"), ("Rice", "rice"), ("Minimal", "minimal")],
            value=str(_setting("tui_theme", "auto")),
            id="theme-select",
        )
        yield Static("Colour-blind mode", classes="settings-label")
        yield Switch(value=_bool_setting("colour_blind_mode"), id="colour-blind")
        yield Static("Esc back · Ctrl+Q quit", id="footer")

    def _account_status(self) -> str:
        try:
            from core import exe_fingerprinter_api as api

            s = api.status()
            if s.get("has_token"):
                return f"Signed in as {s.get('username', '')} · HWID locked to this device"
            if s.get("configured"):
                return "Configured but not signed in — press Sign in."
            return "Not signed in — enter credentials and press Sign in."
        except Exception as exc:
            logger.debug("[Settings] account status unavailable: %s", exc)
            return "Private API not configured."

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    @work(thread=True, exclusive=True, group="signin")
    def _sign_in(self) -> None:
        base = self.query_one("#api-base", Input).value.strip()
        user = self.query_one("#api-user", Input).value.strip()
        password = self.query_one("#api-pass", Input).value
        try:
            from core import exe_fingerprinter_api as api

            api.save_config(base_url=base, username=user, password=password)
            result = api.login(base_url=base, username=user, password=password)
            ok, detail = result.ok, ((result.data or {}) if result.ok else str(result.error))
        except Exception as exc:
            ok, detail = False, str(exc)
        self.app.call_from_thread(self._signin_done, ok, detail)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        from utils.settings import get_settings

        settings = get_settings()
        widget_id = event.input.id
        value = event.value.strip()
        if widget_id == "hubcap-key":
            settings.setValue("morrenus_api_key", value)
        elif widget_id == "api-base":
            settings.setValue("exefp_api_base", value)
        elif widget_id == "api-user":
            settings.setValue("exefp_api_username", value)
        elif widget_id == "api-pass":
            settings.setValue("exefp_api_password", value)
        settings.sync()
        self.query_one("#settings-status", Static).update("Saved.")
        self.query_one("#settings-status", Static).remove_class("-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-signin":
            self.query_one("#settings-status", Static).update("Signing in…")
            self._sign_in()

    def _signin_done(self, ok: bool, detail) -> None:
        status = self.query_one("#settings-status", Static)
        if ok:
            status.update(f"Signed in as {detail.get('username', '')}.")
            status.remove_class("-error")
        else:
            status.update(f"Sign-in failed: {detail}")
            status.add_class("-error")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "theme-select":
            from utils.settings import get_settings

            settings = get_settings()
            settings.setValue("tui_theme", str(event.value))
            settings.sync()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "colour-blind":
            from utils.settings import get_settings

            settings = get_settings()
            settings.setValue("colour_blind_mode", bool(event.value))
            settings.sync()
