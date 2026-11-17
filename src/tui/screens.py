"""Textual screens for EXE-fingerprinter — ported from paws' layout.

paws' interface style, rebound to EXE-fingerprinter's own features:

* ``HomeScreen``   — app header with banner, an ASCII art panel, and a menu.
* ``SearchScreen`` — find a game by AppID or short name (Store, SteamDB, Hubcap, your API).
* ``GameScreen``   — fingerprint the base game (manifest-only), tick executables, download.
* ``SettingsScreen``— API keys (Hubcap + your private API), theme, colour-blind mode.
* ``AboutScreen``  — what this is.

``--api`` starts on the same home screen with a fingerprint-only header; no games
are ever added or removed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListView,
    OptionList,
    ProgressBar,
    Select,
    SelectionList,
    Static,
    Switch,
    ListItem,
)
from textual.widgets.option_list import Option

from . import art, settings
from .widgets.art_panel import AsciiPanel
from .widgets.footer import FooterHelp
from .widgets.gradient import gradient_text
from .widgets.header import AppHeader
from .widgets.jobbar import JobBar
from .widgets.menu import Menu, MenuItem, restore_index

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


def _is_api_mode() -> bool:
    import sys

    return "--api" in sys.argv or "--api-mode" in sys.argv


def _header_title() -> str:
    return "API mode — fingerprint only" if _is_api_mode() else None


DETAIL_KEYS = "\n".join(
    [
        "[#00d0ff]" + "─" * 28 + "[/]",
        "[#9db0e0]shortcuts[/]",
        "[#dbe4ff]↑ ↓[/]  [#9db0e0]move[/]",
        "[#dbe4ff]enter[/]  [#9db0e0]run[/]",
        "[#dbe4ff]q / esc[/]  [#9db0e0]back · quit[/]",
        "[#dbe4ff]f2[/]  [#9db0e0]settings[/]",
        "",
        "[#9db0e0]the menu on the left picks screens;[/]",
        "[#9db0e0]the panel on the right explains it.",
    ]
)


# ── home screen ───────────────────────────────────────────────────────────

class MenuPanel(Container):
    DETAIL_MIN = 86
    HINTS_MIN = 44

    def on_resize(self, event):
        self.query_one("#menu-detail").display = self.size.width >= self.DETAIL_MIN and not settings.is_minimal()
        compact = self.size.width < self.HINTS_MIN
        if compact != self.has_class("-compact"):
            self.set_class(compact, "-compact")


class HomeScreen(Screen):
    HOME_MENU = tuple(
        (key, label, desc, icon, hint)
        for key, label, desc, icon, hint in [
            ("search", "Search & fingerprint", "Type an AppID or short name — store, SteamDB, Hubcap, your API. Found games get fingerprinted; only the .exe files download.", "search", "steam store · pick files"),
            ("settings", "Settings", "API keys (Hubcap + your private API), theme, colour-blind mode. Sign in once — the account locks to this device.", "settings", "keys + look"),
            ("about", "About", "What this is and how it works: manifest-only fingerprint, filelist-limited download, SFW assets.", "about", "what & why"),
            ("quit", "Quit", "Exit EXE-fingerprinter", "quit", "esc or q"),
        ]
    )

    BINDINGS = [
        ("ctrl+q", "quit", "quit"),
        ("q", "quit", "quit"),
        ("slash", "search", "search"),
        ("f2", "settings", "settings"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="home-body"):
            with Container(id="art-zone"):
                yield AsciiPanel(id="art-home")
            with Vertical(id="right-col"):
                yield AppHeader(_header_title(), id="hdr")
                with MenuPanel(id="menu-panel"):
                    yield Static(gradient_text("MENU " + "· " * 6), id="menu-panel-title")
                    with Horizontal(id="menu-body"):
                        yield Menu(id="menu", classes="in-panel")
                        with Vertical(id="menu-detail"):
                            yield Static("", id="detail-top", markup=True)
                            yield Static(DETAIL_KEYS, id="detail-keys", markup=True)
                yield JobBar(id="job", classes="thin")
                yield FooterHelp("pick something (up/down, enter, q to quit)")

    def on_mount(self) -> None:
        self._build_menu()
        self._refresh_art()

    def _refresh_art(self) -> None:
        if settings.is_minimal():
            self.query_one("#art-zone").display = False
            return
        try:
            name = art.random_pick(tier="sfw", nsfw=False) or "placeholder.txt"
        except Exception:
            name = "placeholder.txt"
        self.query_one(AsciiPanel).show(name)

    def _build_menu(self) -> None:
        menu = self.query_one("#menu", ListView)
        menu.clear()
        for key, label, desc, icon_name, hint in self.HOME_MENU:
            menu.append(MenuItem(key, label, desc, icon_name, hint=hint))
        restore_index(menu, 0)
        self._set_detail(self.HOME_MENU[0][2])

    def _set_detail(self, desc: str) -> None:
        self.query_one("#detail-top", Static).update(f"[b]{desc}[/b]")

    def action_search(self) -> None:
        self.app.push_screen(SearchScreen())

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or not (0 <= index < len(self.HOME_MENU)):
            return
        key = self.HOME_MENU[index][0]
        if key == "search":
            self.app.push_screen(SearchScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        elif key == "about":
            self.app.push_screen(AboutScreen())
        elif key == "quit":
            self.app.exit()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        index = event.list_view.index
        if index is None or not (0 <= index < len(self.HOME_MENU)):
            return
        self._set_detail(self.HOME_MENU[index][2])


# ── search screen ─────────────────────────────────────────────────────────

class SearchScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("slash", "focus_search", "search"),
        ("f2", "settings", "settings"),
        ("ctrl+q", "quit", "quit"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader(_header_title(), id="hdr")
        yield Input(placeholder="AppID or game name…", id="search-input")
        yield Static("Search by AppID or name — Enter searches, ↑↓ moves, Enter opens.", id="search-hint")
        yield OptionList(id="results")
        yield FooterHelp("esc back · ↑↓ move · enter open · f2 settings")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).value = ""
        self.query_one("#search-input", Input).focus()
        self._results: List[Dict[str, Any]] = []

    def _search(self, query: str) -> None:
        self.app.run_worker(
            lambda: self._do_search(query),
            group="search",
            thread=True,
            exclusive=True,
        )

    @work(thread=True, exclusive=True, group="search")
    def _do_search(self, query: str) -> None:
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
        if busy:
            self.query_one("#search-hint", Static).update("Searching…")

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
        hint.update(f"{len(results)} result(s) — Enter opens, ↑↓ moves.")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._search(event.value.strip())

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_quit(self) -> None:
        self.app.exit()

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
        ("f2", "settings", "settings"),
    ]

    def __init__(self, appid: int, name: str) -> None:
        super().__init__()
        self.appid = int(appid)
        self.app_name = name

    def compose(self) -> ComposeResult:
        yield AppHeader(f"App {self.appid} — {self.app_name or '…'}", id="hdr")
        yield Static(
            "Base game only · DLC skipped · manifest-only scan (downloads no game content)",
            id="app-subtitle",
        )
        yield Static("Press F (or the button) to fingerprint this game.", id="scan-status")
        yield SelectionList(id="exe-list")
        with Horizontal(id="button-row"):
            yield Button("Fingerprint (F)", id="btn-fingerprint", variant="primary")
            yield Button("Download .exes (D)", id="btn-download", variant="success", disabled=True)
        yield FooterHelp("F fingerprint · D download · A toggle all · esc back")

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

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fingerprint":
            self._start_scan()
        elif event.button.id == "btn-download":
            self._start_download()

    @work(thread=True, exclusive=True, group="scan")
    def _start_scan(self) -> None:
        self.app.call_from_thread(self._set_busy, True, "Scanning Windows base-game depots…")
        self.app.job.start("fingerprinting the base game")

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
            status.update(f"{len(self._entries)} executable(s) — tick what you want, then press D.")
        self.app.job.done()
        self._set_busy(False)
        self.query_one("#btn-download", Button).disabled = not self._entries

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
        self.app.job.start(f"downloading {len(picked)} file(s)")
        dest = engine.default_destination(str(self.appid))
        count, errors = engine.download_selected(
            self._scan,
            picked,
            destination=dest,
            progress=lambda line: self._progress(line[:140]),
        )
        self.app.job.done()
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
        yield AppHeader("settings", id="hdr")
        yield Static("API keys & appearance — stored locally on this device only.", id="app-subtitle")
        yield Static("Hubcap API key", classes="settings-label")
        yield Input(
            value=str(_setting("morrenus_api_key", "")),
            placeholder="fetches manifest bundles for the fingerprint",
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
        yield Input(
            value=str(_setting("exefp_api_password", "")),
            password=True,
            placeholder="password",
            id="api-pass",
            classes="settings-input",
        )
        with Horizontal(id="set-row"):
            yield Button("Sign in", id="btn-signin")
        yield Static(self._account_status(), id="settings-status")
        yield Static("Theme (Auto = rice on colour terminals, minimal otherwise)", classes="settings-label")
        yield Select(
            [("Auto", "auto"), ("Rice", "rice"), ("Minimal", "minimal")],
            value=str(_setting("tui_theme", "auto")),
            id="theme-select",
        )
        yield Static("Colour-blind mode", classes="settings-label")
        yield Switch(value=settings.colorblind_enabled(), id="colour-blind")
        yield FooterHelp("esc back · enter saves a field · ctrl+q quit")

    def _account_status(self) -> str:
        try:
            from core import exe_fingerprinter_api as api

            status = api.status()
            if status.get("has_token"):
                return f"Signed in as {status.get('username', '')} · HWID locked to this device"
            return "Not signed in — enter credentials and press Sign in."
        except Exception as exc:
            logger.debug("[Settings] account status unavailable: %s", exc)
            return "Private API not configured."

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        from utils.settings import get_settings

        saved = get_settings()
        widget_id = event.input.id
        value = event.value.strip()
        if widget_id == "hubcap-key":
            saved.setValue("morrenus_api_key", value)
        elif widget_id == "api-base":
            saved.setValue("exefp_api_base", value)
        elif widget_id == "api-user":
            saved.setValue("exefp_api_username", value)
        elif widget_id == "api-pass":
            saved.setValue("exefp_api_password", value)
        saved.sync()
        status = self.query_one("#settings-status", Static)
        status.update("Saved.")
        status.remove_class("-error")

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
            settings.set_colorblind("deuteranopia" if event.value else "off")


# ── about screen ─────────────────────────────────────────────────────────

class AboutScreen(Screen):
    BINDINGS = [("escape", "back", "back")]

    def compose(self) -> ComposeResult:
        yield AppHeader("about", id="hdr")
        yield Static(
            "[b]EXE-fingerprinter 3.0.0[/b]\n\n"
            "A TUI that fingerprints a Steam game's base-game Windows depots\n"
            "and downloads just the executables — nothing else.\n\n"
            "depot keys  →  local key database → your private API → Hubcap\n"
            "manifests   →  steamcmd / PICS (DLC depots skipped)\n"
            "downloads   →  DepotDownloader -filelist of the .exe files you tick\n\n"
            "SFW assets only. Unofficial — not affiliated with Valve or Steam.\n\n"
            "[#9db0e0]Esc back[/]",
            id="about-text",
        )
        yield FooterHelp("esc back")

    def action_back(self) -> None:
        self.app.pop_screen()
