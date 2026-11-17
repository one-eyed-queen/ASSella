"""Textual screens for EXE-fingerprinter — ported from paws' layout.

paws' interface style, rebound to EXE-fingerprinter's own features:

* ``HomeScreen``   — app header with banner, an ASCII art panel, and a menu.
* ``SearchScreen`` — find a game by AppID or short name (Store, SteamDB, Hubcap, your API).
* ``GameScreen``   — fingerprint the base game (manifest-only), tick executables, download.
* ``SettingsScreen``— API keys (Hubcap + your private API), theme.
* ``AboutScreen``  — what this is.

``--api`` starts on the same home screen with a fingerprint-only header; no games
are ever added or removed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    ListItem,
    ListView,
    Select,
    SelectionList,
    Static,
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
            ("external", "External fingerprint", "Fingerprint any .exe — pick it with the native file picker, get the full report (hashes, PE layout, imports, version info, disasm) and copy or export the TOML.", "fingerprint", "any .exe · full report"),
            ("settings", "Settings", "API keys (Hubcap + your private API), theme. Sign in once — the account locks to this device.", "settings", "keys + look"),
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
        elif key == "external":
            self.app.push_screen(ExternalFingerprintScreen())
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

RESULT_COLUMNS = ("appid", "name", "price", "kind")


class SearchScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("slash", "focus_search", "search"),
        ("f2", "settings", "settings"),
        ("ctrl+q", "quit", "quit"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader(_header_title(), id="hdr")
        with Horizontal(id="add-split"):
            with Vertical(id="add-left"):
                yield Input(placeholder="game name or AppID (try 480, or 'portal')", id="search-input")
                yield DataTable(id="results-table", cursor_type="row", zebra_stripes=True)
                yield Static("Enter searches · ↑↓ moves · Enter picks a game", id="add-hint", classes="panel")
            with VerticalScroll(id="add-detail", classes="panel"):
                yield Static("[b]pick a game[/]", id="detail-info", markup=True)
                from .widgets import hires

                self._img_widget = hires.picture(None, id="detail-img") if hires.available() else None
                if self._img_widget is not None:
                    self._img_widget.styles.width = 34
                    self._img_widget.styles.height = "auto"
                    yield self._img_widget
                yield Static("", id="detail-desc", markup=False)
        yield FooterHelp("esc back · ↑↓ move · enter choose · f2 settings")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns(*RESULT_COLUMNS)
        self.query_one("#search-input", Input).value = ""
        self.query_one("#search-input", Input).focus()
        self._results: List[Dict[str, Any]] = []
        self._kinds: Dict[int, str] = {}
        self._details_token = 0

    def _table(self) -> DataTable:
        return self.query_one("#results-table", DataTable)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._search(event.value.strip())

    def _search(self, query: str) -> None:
        if not query:
            return
        self.query_one(Input).disabled = True
        self.query_one("#add-hint", Static).update("Searching…")
        self.run_worker(
            lambda: self._do_search(query),
            group="search",
            thread=True,
            exclusive=True,
        )

    @work(thread=True, exclusive=True, group="search")
    def _do_search(self, query: str) -> None:
        try:
            from core.unified_search import search

            results = search(query, limit=30, include_steamdb=True)
        except Exception as exc:
            logger.warning("[Search] failed: %s", exc)
            results = []
        self.app.call_from_thread(self._render_results, query, results)

    def _render_results(self, query: str, results: List[Dict[str, Any]]) -> None:
        self._results = results
        table = self._table()
        table.clear()
        hint = self.query_one("#add-hint", Static)
        self.query_one(Input).disabled = False
        if not results:
            hint.update(f"No results for '{query}'.")
            return
        for index, entry in enumerate(results):
            price = entry.get("price") or ""
            table.add_row(
                str(entry.get("appid", "")),
                str(entry.get("name", "")),
                str(price).replace("[", "(").replace("]", ")"),
                str(entry.get("kind", "")),
                key=str(index),
            )
        hint.update(f"{len(results)} result(s) — Enter chooses.")

    @on(DataTable.RowHighlighted, "#results-table")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            index = int(str(event.row_key.value))
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._results)):
            return
        self._details_token += 1
        token = self._details_token
        self.run_worker(lambda: self._inspect_later(index, token), group="search-inspect", thread=True)

    def _inspect_later(self, index: int, token: int) -> None:
        from core.unified_search import app_details

        entry = self._results[index]
        details = app_details(int(entry["appid"]))
        if details:
            entry.update({k: v for k, v in details.items() if v})
        self.app.call_from_thread(self._show_detail, index, token, entry)

    def _show_detail(self, index: int, token: int, entry: Dict[str, Any]) -> None:
        if token != self._details_token:
            return
        title = f"[b]{entry.get('appid')}[/b]  {entry.get('name', '')}"
        price = "[#5ff0a0]free[/]" if entry.get("free") else (f"[#5ff0a0]{entry.get('price')}[/]" if entry.get("price") else "")
        self.query_one("#detail-info", Static).update(f"{title}\n{price}" if price else title)
        self._show_image(entry.get("header_image") or entry.get("header_url") or "")
        devs = ", ".join(entry.get("developers") or [])
        desc = (entry.get("short_description") or "").strip()
        if devs:
            desc = f"dev: {devs}\n\n{desc}"
        self.query_one("#detail-desc", Static).update(desc)

    def _show_image(self, url: str) -> None:
        from . import imgpreview

        if self._img_widget is None:
            return
        if not url:
            self._img_widget.display = False
            return
        image = imgpreview.get_header_image(url)
        if image is None:
            self._img_widget.display = False
            return
        self._img_widget.display = True
        self._img_widget.image = image

    @on(DataTable.RowSelected, "#results-table")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            index = int(str(event.row_key.value))
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._results)):
            return
        entry = self._results[index]
        self.app.push_screen(ChoiceScreen(appid=int(entry["appid"]), name=str(entry.get("name") or "")))


# ── fingerprint vs download choice ──────────────────────────────────────────

class ChoiceScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("f", "fingerprint", "fingerprint"),
        ("d", "download", "download"),
    ]

    def __init__(self, appid: int, name: str) -> None:
        super().__init__()
        self.appid = int(appid)
        self.app_name = name

    def compose(self) -> ComposeResult:
        yield AppHeader(f"App {self.appid} — {self.app_name or '…'}", id="hdr")
        yield Static(
            "What do you want to do with this game?",
            id="app-subtitle",
        )
        with Vertical(id="choice-buttons"):
            yield Button("Fingerprint (F)  ·  analyze the .exes", id="btn-fingerprint", variant="primary")
            yield Button("Download (D)  ·  pick destination", id="btn-download", variant="success")
        yield FooterHelp("f fingerprint · d download · esc back")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_fingerprint(self) -> None:
        self.app.push_screen(FingerprintTaskScreen(appid=self.appid, name=self.app_name))

    def action_download(self) -> None:
        self.app.push_screen(GameScreen(appid=self.appid, name=self.app_name))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fingerprint":
            self.action_fingerprint()
        elif event.button.id == "btn-download":
            self.action_download()


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
            yield Button("Download to folder… (D)", id="btn-download", variant="success", disabled=True)
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
        self._pick_destination()

    def action_toggle_all(self) -> None:
        self.query_one("#exe-list", SelectionList).toggle_all()

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fingerprint":
            self._start_scan()
        elif event.button.id == "btn-download":
            self._pick_destination()

    @work(thread=True, exclusive=True, group="dest-pick")
    def _pick_destination(self) -> None:
        from .native_pick import pick_folder

        dest = pick_folder(f"Where should the App {self.appid} .exe files go?")
        self.app.call_from_thread(self._destination_picked, dest)

    def _destination_picked(self, dest: Optional[str]) -> None:
        if not dest:
            dest = engine.default_destination(str(self.appid))
        self._start_download(dest)

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
    def _start_download(self, dest: Optional[str] = None) -> None:
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
        if not dest:
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


# ── fingerprint task + report ───────────────────────────────────────────────

class FingerprintTaskScreen(Screen):
    BINDINGS = [("escape", "back", "back")]

    def __init__(self, appid: int, name: str) -> None:
        super().__init__()
        self.appid = int(appid)
        self.app_name = name

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Fingerprinting App {self.appid} — {self.app_name}", id="hdr")
        yield Static("Downloading the .exe files to a temp folder…", id="scan-status")
        yield Static("", id="scan-detail")
        yield FooterHelp("esc cancel")

    def on_mount(self) -> None:
        self._cancelled = False
        self.run_worker(self._run, thread=True, group="fingerprint")

    def action_back(self) -> None:
        self._cancelled = True
        self.app.pop_screen()

    @work(thread=True, exclusive=True, group="fingerprint")
    def _run(self) -> None:
        import tempfile

        appid = str(self.appid)
        self.app.job.start(f"fingerprinting App {appid}")

        def _progress(message, step=0, total=1):
            if self._cancelled:
                return
            self.app.call_from_thread(self.query_one("#scan-detail", Static).update, str(message)[:140])

        try:
            scan = engine.scan_app(appid, progress=_progress)
        except Exception as exc:
            logger.warning("[Fingerprint] scan failed: %s", exc)
            scan = None

        if self._cancelled:
            return

        if scan is None or not scan.entries:
            errors = list((scan.errors if scan else [])) or ["No executables found."]
            self.app.job.done()
            self.app.call_from_thread(self._failed, errors)
            return

        temp_dir = tempfile.mkdtemp(prefix=f"exe-fingerprinter-{appid}-")
        progress = lambda msg: self.app.call_from_thread(
            self.query_one("#scan-status", Static).update, str(msg)[:140]
        )
        count, errors = engine.download_selected(scan, scan.entries, destination=temp_dir, progress=progress)
        engine.cleanup_keys(scan)
        if self._cancelled:
            return

        from core.fingerprint import fingerprint_file

        reports = []
        for path in sorted(Path(temp_dir).glob("**/*")):
            if not path.is_file() or path.suffix.lower() != ".exe":
                continue
            try:
                reports.append(fingerprint_file(str(path), source=f"steam appid {appid}"))
            except Exception as exc:
                logger.warning("[Fingerprint] analysis failed for %s: %s", path, exc)
        self.app.job.done()
        self.app.call_from_thread(self._done, reports, temp_dir, errors)

    def _failed(self, errors: List[str]) -> None:
        self.query_one("#scan-status", Static).update("Fingerprint failed:")
        self.query_one("#scan-detail", Static).update("\n".join(errors)[:200])

    def _done(self, reports: List[Dict[str, Any]], temp_dir: str, errors: List[str]) -> None:
        if not reports:
            self._failed(errors or ["No executables were downloaded to analyze."])
            return
        self.app.pop_screen()
        # One report screen per executable: Copy / Export work per file.
        for report in reports:
            self.app.push_screen(FingerprintScreen(report=report, work_dir=temp_dir))


class FingerprintScreen(Screen):
    """Report for a single .exe: everything a user-mode app can get from it."""

    BINDINGS = [
        ("escape", "back", "back"),
        ("ctrl+q", "quit", "quit"),
        ("c", "copy", "copy"),
        ("e", "export", "export"),
    ]

    def __init__(self, report: Dict[str, Any], work_dir: Optional[str] = None) -> None:
        super().__init__()
        self.report = report
        self.work_dir = work_dir or str(Path(report.get("file", {}).get("path", "")).parent)

    def compose(self) -> ComposeResult:
        name = self.report.get("file", {}).get("name", ".exe")
        yield AppHeader(f"fingerprint · {name}", id="hdr")
        with VerticalScroll(id="report"):
            yield Static("", id="report-text", markup=False)
        with Horizontal(id="button-row"):
            yield Button("Copy TOML", id="btn-copy", variant="primary")
            yield Button("Export TOML", id="btn-export", variant="success")
        yield Static("", id="fingerprint-status")
        yield FooterHelp("c copy · e export · esc done")

    def on_mount(self) -> None:
        from core.fingerprint import fingerprint_to_text

        lines = fingerprint_to_text(self.report)
        self.query_one("#report-text", Static).update("\n".join(lines))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    def action_copy(self) -> None:
        self._copy()

    def action_export(self) -> None:
        self._export()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-copy":
            self._copy()
        elif event.button.id == "btn-export":
            self._export()

    def _set_status(self, text: str, ok: bool = True) -> None:
        status = self.query_one("#fingerprint-status", Static)
        status.update(text)
        status.remove_class("-error") if ok else status.add_class("-error")

    def _copy(self) -> None:
        from core.fingerprint import fingerprint_to_toml

        from .native_pick import copy_to_clipboard

        ok = copy_to_clipboard(fingerprint_to_toml(self.report))
        self._set_status("Copied TOML to the clipboard." if ok else "Clipboard copy failed — install xclip/wl-copy.", ok)

    def _export(self) -> None:
        from core.fingerprint import fingerprint_to_toml

        name = Path(self.report.get("file", {}).get("name", "file")).stem or "file"
        out = Path(self.work_dir) / f"{name}.fingerprint.toml"
        try:
            out.write_text(fingerprint_to_toml(self.report), encoding="utf-8")
        except OSError as exc:
            self._set_status(f"Export failed: {exc}", False)
            return
        self._set_status(f"Wrote {out}")


class ExternalFingerprintScreen(Screen):
    """Pick any .exe and fingerprint it — no game depots involved."""

    BINDINGS = [("escape", "back", "back")]

    def compose(self) -> ComposeResult:
        yield AppHeader("external fingerprint", id="hdr")
        yield Static("Choose any .exe — one native file picker.", id="scan-status")
        with Horizontal(id="button-row"):
            yield Button("Pick .exe…", id="btn-pick", variant="primary")
        yield FooterHelp("esc back")

    def on_mount(self) -> None:
        self.run_worker(self._pick, thread=True, group="exe-pick")

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-pick":
            self._pick()

    @work(thread=True, exclusive=True, group="exe-pick")
    def _pick(self) -> None:
        from .native_pick import pick_executable

        path = pick_executable("Fingerprint which .exe?")
        self.app.call_from_thread(self._picked, path)

    def _picked(self, path: Optional[str]) -> None:
        if not path:
            self.query_one("#scan-status", Static).update("No file selected — press Pick .exe… or Esc.")
            return
        from core.fingerprint import fingerprint_file

        report = fingerprint_file(path, source="external file")
        self.app.pop_screen()
        self.app.push_screen(FingerprintScreen(report=report, work_dir=str(Path(path).parent)))


# ── settings screen ────────────────────────────────────────────────────────

class SettingsScreen(Screen):
    BINDINGS = [
        ("escape", "back", "back"),
        ("ctrl+q", "quit", "quit"),
        ("up", "nav_up", "up"),
        ("down", "nav_down", "down"),
        ("left", "nav_left", "left"),
        ("right", "nav_right", "right"),
    ]

    _FOCUS_ORDER = [
        "hubcap-key",
        "api-base",
        "api-user",
        "api-pass",
        "btn-signin",
        "theme-select",
    ]

    _THEME_ORDER = ["auto", "rice", "minimal"]

    def _focus_ids(self) -> list[str]:
        return [w_id for w_id in self._FOCUS_ORDER if self.query(f"#{w_id}")]

    def _focused_id(self):
        focused = self.focused
        return getattr(focused, "id", None) if focused else None

    def _move_focus(self, delta: int) -> None:
        ids = self._focus_ids()
        current = self._focused_id()
        index = ids.index(current) if current in ids else (0 if delta > 0 else len(ids) - 1)
        target = ids[(index + delta) % len(ids)]
        try:
            self.query_one(f"#{target}").focus()
        except Exception:
            pass

    def action_nav_up(self) -> None:
        self._move_focus(-1)

    def action_nav_down(self) -> None:
        self._move_focus(1)

    def action_nav_left(self) -> None:
        focused = self.focused
        if getattr(focused, "id", None) == "theme-select":
            self._cycle_theme(-1)

    def action_nav_right(self) -> None:
        focused = self.focused
        if getattr(focused, "id", None) == "theme-select":
            self._cycle_theme(1)

    def _cycle_theme(self, delta: int) -> None:
        from utils.settings import get_settings

        current = str(get_settings().value("tui_theme", "auto") or "auto").strip().lower()
        index = self._THEME_ORDER.index(current) if current in self._THEME_ORDER else 0
        target = self._THEME_ORDER[(index + delta) % len(self._THEME_ORDER)]
        select = self.query_one("#theme-select", Select)
        select.value = target
        get_settings().setValue("tui_theme", target)
        get_settings().sync()

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
        yield FooterHelp("↑↓ move · ←→ change theme · esc back · enter saves a field")

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
