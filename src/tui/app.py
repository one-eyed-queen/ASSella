"""EXE-fingerprinter TUI.

A terminal counterpart to the desktop window: search, fingerprint a game's
Windows executables, pick the ones to keep, and download only those. Sixel
graphics are used for the splash where the terminal supports them; otherwise
the same header is drawn as text.

Keys are shown in the footer and change per screen. ``Esc`` always goes back,
``Ctrl+C`` always quits.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.align import Align
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tui import engine, sixel
from tui.terminal import RawTerminal, read_key, terminal_size
from tui.theme import Theme, resolve_theme, _setting

SETTINGS_ITEMS = [
    ("theme", "TUI theme"),
    ("colour_blind", "Colour-blind mode"),
    ("sixel", "Sixel graphics"),
    ("sfw", "SFW-only artwork"),
    ("api_base", "API base URL"),
    ("api_user", "API username"),
    ("api_password", "API password"),
    ("sign_in", "Sign in / refresh token"),
    ("back", "Back"),
]

EDITABLE = {"api_base", "api_user", "api_password"}


def _set_setting(key: str, value) -> None:
    from utils.settings import get_settings

    settings = get_settings()
    settings.setValue(key, value)
    settings.sync()


def _bool_setting(key: str, default: bool = False) -> bool:
    raw = _setting(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def _size(value: int) -> str:
    if not value:
        return "?"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


@dataclass
class AppChoice:
    appid: int
    name: str
    sources: List[str] = field(default_factory=list)


class FingerprinterTUI:
    def __init__(self, argv: Optional[List[str]] = None):
        self.argv = list(argv or sys.argv)
        self.console = Console(highlight=False, soft_wrap=False)

        self.theme_name = self._arg_value("--theme", "--tui-theme")
        self.theme: Theme = resolve_theme(self.theme_name)
        self.api_mode = "--api" in self.argv or "--api-mode" in self.argv
        self.auto_download = "--download" in self.argv
        self.destination = self._arg_value("--dest", "--destination")
        self.initial_appid = self._arg_value("--appid")
        self.no_sixel = "--no-sixel" in self.argv

        self.view = "search"
        self.running = True
        self.dirty = True
        self.busy = False
        self.status_message = ""

        # search
        self.query = ""
        self.searched_query = ""
        self.results: List[AppChoice] = []
        self.result_index = 0
        self.search_error = ""

        # game
        self.appid: Optional[int] = None
        self.app_name = ""
        self.game_info: Dict[str, Any] = {}
        self.game_loading = False

        # scan
        self.scan: Optional[engine.ScanResult] = None
        self.scan_message = ""
        self.scan_step = 0
        self.scan_total = 0
        self.scan_errors: List[str] = []

        # exes
        self.exe_index = 0
        self.exe_checked: set[int] = set()

        # download
        self.download_log: List[str] = []
        self.download_result: Optional[tuple] = None
        self.download_dir = ""

        # settings
        self.settings_index = 0
        self.editing: Optional[dict] = None
        self.settings_status = ""
        self.api_choice_index = 0

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self._splash_done = False

    # ── small helpers ──────────────────────────────────────────────────────
    def _arg_value(self, *names: str) -> Optional[str]:
        for index, arg in enumerate(self.argv):
            for name in names:
                if arg == name and index + 1 < len(self.argv):
                    return self.argv[index + 1]
                if arg.startswith(name + "="):
                    return arg.split("=", 1)[1]
        return None

    def _style(self, value: str) -> str:
        return value

    def _maybe_sixel_splash(self) -> None:
        if self._splash_done or self.api_mode or self.no_sixel:
            return
        if not _bool_setting("tui_sixel", True):
            return
        if not sixel.supports_sixel():
            return
        logo = sixel.find_logo()
        if not logo:
            return
        width, _ = terminal_size()
        cells = max(20, min(44, width // 3))
        data = sixel.render_banner(logo, width_cells=cells, background=self.theme.background)
        if not data:
            return
        try:
            self.console.clear()
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            self.console.print()
            self.console.print(
                Align.center(
                    Text("EXE-fingerprinter", style=f"bold {self.theme.accent}")
                )
            )
            self.console.print(
                Align.center(Text("press any key", style=self.theme.muted))
            )
            self.dirty = True
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                if read_key(0.1):
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._splash_done = True

    def _footer(self, keys: List[tuple[str, str]]) -> Text:
        text = Text()
        for index, (key, label) in enumerate(keys):
            if index:
                text.append("   ", style=self.theme.muted)
            text.append(f" {key} ", style=f"bold black on {self.theme.accent}")
            text.append(f" {label}", style=self.theme.muted)
        return text

    def _status(self) -> Text:
        message = getattr(self, "status_message", "")
        if self.search_error and self.view == "search":
            message = self.search_error
        if not message:
            message = "Type an AppID or a game name, then press Enter." if self.view == "search" else ""
        colour = self.theme.error if self.search_error and self.view == "search" else self.theme.muted
        return Text(message, style=colour)

    def _header(self) -> Panel:
        mode = "API" if self.api_mode else "TUI"
        title = Text()
        title.append("EXE", style=f"bold {self.theme.accent}")
        title.append("-fingerprinter", style=f"bold {self.theme.text}")
        subtitle = Text(
            "executable fingerprinting · " + ("API mode" if self.api_mode else "search & fetch"),
            style=self.theme.muted,
        )
        right = Text(f"{mode}  ", style=f"bold {self.theme.accent_alt}")
        line = Table.grid(expand=True)
        line.add_column(ratio=1)
        line.add_column(justify="right")
        line.add_row(Group(title, subtitle), right)
        return Panel(line, border_style=self.theme.border, style=f"on {self.theme.panel}", padding=(0, 1))

    # ── search ─────────────────────────────────────────────────────────────
    def _render_search(self) -> Group:
        prompt = Text()
        prompt.append("Search  ", style=f"bold {self.theme.accent}")
        prompt.append(self.query or "", style=self.theme.text)
        prompt.append("▏", style=self.theme.accent)

        body: List[Any] = [prompt, Text("")]

        if not self.results:
            hint = (
                "Search by AppID or name. Sources: Steam Store, SteamDB, Hubcap, and the API."
                if not self.api_mode
                else "Enter an AppID or game name. API mode only fingerprints; it does not add games."
            )
            body.append(Text(hint, style=self.theme.muted))
            if self.busy:
                spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 10) % 10]
                body.append(Text(f"{spinner} searching…", style=self.theme.accent))
        else:
            table = Table(
                box=None,
                expand=True,
                padding=(0, 1),
                show_header=True,
                header_style=f"bold {self.theme.muted}",
            )
            table.add_column("#", width=3, justify="right", style=self.theme.muted)
            table.add_column("AppID", width=9, justify="right", style=self.theme.accent_alt)
            table.add_column("Name", ratio=1)
            table.add_column("Sources", width=24, style=self.theme.muted)

            height = max(6, terminal_size()[1] - 15)
            start = max(0, self.result_index - height + 3)
            visible = self.results[start : start + height]
            for offset, entry in enumerate(visible):
                index = start + offset
                selected = index == self.result_index
                row_style = f"on {self.theme.selection}" if selected else ""
                marker = "❯" if selected else " "
                table.add_row(
                    marker,
                    str(entry.appid),
                    escape(entry.name or "(unknown)"),
                    escape(", ".join(entry.sources or [])),
                    style=row_style,
                )
            body.append(table)
            body.append(
                Text(
                    f"{self.result_index + 1} of {len(self.results)} — Enter opens the game",
                    style=self.theme.muted,
                )
            )

        return Group(
            self._header(),
            Text(""),
            *body,
            Text(""),
            self._status(),
            Text(""),
            self._footer(
                [
                    ("Enter", "search / open"),
                    ("↑↓", "move"),
                    ("F2", "settings"),
                    ("Ctrl+C", "quit"),
                ]
            ),
        )

    # ── game ───────────────────────────────────────────────────────────────
    def _render_game(self) -> Group:
        title = Text(self.app_name or f"AppID {self.appid}", style=f"bold {self.theme.text}")
        meta = Text()
        meta.append(f"AppID {self.appid}", style=self.theme.accent_alt)
        if self.game_loading:
            meta.append("   loading metadata…", style=self.theme.muted)
        elif self.game_info:
            build = self.game_info.get("buildid") or ""
            depots = self.game_info.get("depots") or {}
            installed = self.game_info.get("installdir") or ""
            meta.append(f"   build {build or '?'}", style=self.theme.muted)
            meta.append(f"   {len(depots)} depot(s)", style=self.theme.muted)
            if installed:
                meta.append(f"   {installed}", style=self.theme.muted)

        lines: List[Any] = [title, meta, Text("")]
        if not self.scan:
            lines.append(
                Text(
                    "Press F to scan every Windows depot for .exe files.",
                    style=self.theme.muted,
                )
            )
        else:
            lines.append(
                Text(
                    f"Fingerprint: {len(self.scan.entries)} executable(s) across "
                    f"{len(self.scan.plans)} depot(s).",
                    style=self.theme.success,
                )
            )
            if self.scan.errors:
                lines.append(
                    Text(f"{len(self.scan.errors)} depot(s) could not be read.", style=self.theme.warning)
                )

        lines += [
            Text(""),
            self._status(),
            Text(""),
            self._footer(
                [
                    ("F", "fingerprint"),
                    ("E", "choose .exe files"),
                    ("O", "open desktop UI"),
                    ("Esc", "back"),
                    ("F2", "settings"),
                    ("Ctrl+C", "quit"),
                ]
            ),
        ]
        return Group(self._header(), Text(""), *lines)

    # ── scan progress ──────────────────────────────────────────────────────
    def _render_scan(self) -> Group:
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 10) % 10]
        label = Text()
        label.append(f"{spinner} ", style=f"bold {self.theme.accent}")
        label.append(self.scan_message or "Scanning…", style=self.theme.text)

        total = max(self.scan_total, 1)
        width = max(10, min(60, terminal_size()[0] - 20))
        filled = int(width * min(self.scan_step, total) / total)
        bar = Text()
        bar.append("━" * filled, style=self.theme.accent)
        bar.append("━" * (width - filled), style=self.theme.border)
        bar.append(f"  {self.scan_step}/{self.scan_total}", style=self.theme.muted)

        log = Text("\n".join(self.scan_errors[-6:]), style=self.theme.warning)
        return Group(
            self._header(),
            Text(""),
            label,
            bar,
            Text(""),
            log,
            Text(""),
            self._footer([("Esc", "cancel scan"), ("Ctrl+C", "quit")]),
        )

    # ── executable chooser ─────────────────────────────────────────────────
    def _exe_visible_height(self) -> int:
        return max(5, terminal_size()[1] - 14)

    def _render_exes(self) -> Group:
        entries = self.scan.entries if self.scan else []
        height = self._exe_visible_height()
        start = max(0, self.exe_index - height + 3)
        visible = entries[start : start + height]

        table = Table(
            box=None,
            expand=True,
            padding=(0, 1),
            show_header=True,
            header_style=f"bold {self.theme.muted}",
        )
        table.add_column("", width=2)
        table.add_column("#", width=4, justify="right", style=self.theme.muted)
        table.add_column("Depot", width=9, style=self.theme.accent_alt)
        table.add_column("Path", ratio=1)
        table.add_column("Size", width=10, justify="right", style=self.theme.muted)

        for offset, entry in enumerate(visible):
            index = start + offset
            checked = index in self.exe_checked
            selected = index == self.exe_index
            style = f"on {self.theme.selection}" if selected else ""
            table.add_row(
                "●" if checked else "○",
                str(index + 1),
                str(entry["depot_id"]),
                escape(entry["path"]),
                _size(entry.get("size") or 0),
                style=style,
            )

        chosen = [entries[i] for i in sorted(self.exe_checked) if i < len(entries)]
        total_size = sum(e.get("size") or 0 for e in chosen)
        summary = Text()
        summary.append(f"{len(chosen)} of {len(entries)} selected", style=f"bold {self.theme.accent}")
        summary.append(f"   {_size(total_size)}", style=self.theme.muted)
        if self.api_mode:
            summary.append("   API mode", style=self.theme.accent_alt)

        return Group(
            self._header(),
            Text(""),
            Text(self.app_name or f"AppID {self.appid}", style=f"bold {self.theme.text}"),
            summary,
            Text(""),
            table,
            Text(""),
            self._status(),
            Text(""),
            self._footer(
                [
                    ("Space", "toggle"),
                    ("A", "all"),
                    ("N", "none"),
                    ("D", "download selected"),
                    ("Esc", "back"),
                    ("F2", "settings"),
                    ("Ctrl+C", "quit"),
                ]
            ),
        )

    # ── download log ───────────────────────────────────────────────────────
    def _render_download(self) -> Group:
        done = self.download_result is not None
        if done:
            count, errors = self.download_result or (0, [])
            heading = Text()
            heading.append("Download complete" if not errors else "Download finished with errors", style=f"bold {self.theme.success if not errors else self.theme.warning}")
            body = Text()
            body.append(f"{count} depot(s) downloaded to {self.download_dir}\n", style=self.theme.text)
            for error in errors[-8:]:
                body.append(error + "\n", style=self.theme.error)
        else:
            heading = Text("Downloading executables…", style=f"bold {self.theme.accent}")
            body = Text("\n".join(self.download_log[-14:]), style=self.theme.muted)

        return Group(
            self._header(),
            Text(""),
            heading,
            Text(""),
            body,
            Text(""),
            self._footer(
                [("Esc", "cancel" if not done else "back"), ("Ctrl+C", "quit")]
            ),
        )

    # ── API options ────────────────────────────────────────────────────────
    def _render_api_choice(self) -> Group:
        options = [
            ("Download the .exe files", "run DepotDownloader for the selected executables"),
            ("Use the existing flow", "open the desktop app at the depot selection screen"),
        ]
        table = Table(box=None, expand=True, padding=(0, 1), show_header=False)
        table.add_column("", width=2)
        table.add_column("Option", ratio=1)
        table.add_column("", ratio=1, style=self.theme.muted)
        for index, (label, hint) in enumerate(options):
            selected = index == self.api_choice_index
            table.add_row(
                "❯" if selected else " ",
                label,
                hint,
                style=f"on {self.theme.selection}" if selected else "",
            )
        return Group(
            self._header(),
            Text(""),
            Text(
                f"{self.app_name or 'AppID ' + str(self.appid)} — fingerprint ready",
                style=f"bold {self.theme.text}",
            ),
            Text(
                f"{len(self.scan.entries) if self.scan else 0} executable(s) found. What next?",
                style=self.theme.muted,
            ),
            Text(""),
            table,
            Text(""),
            self._footer(
                [("Enter", "choose"), ("↑↓", "move"), ("Esc", "back"), ("Ctrl+C", "quit")]
            ),
        )

    # ── settings ───────────────────────────────────────────────────────────
    def _settings_values(self) -> Dict[str, str]:
        theme = _setting("tui_theme", "auto") or "auto"
        return {
            "theme": str(theme),
            "colour_blind": "on" if _bool_setting("colour_blind_mode") else "off",
            "sixel": "on" if _bool_setting("tui_sixel", True) else "off",
            "sfw": "on" if _bool_setting("sfw_only", True) else "off",
            "api_base": str(_setting("exefp_api_base", "https://exe-fingerprinter.vercel.app")),
            "api_user": str(_setting("exefp_api_username", "")),
            "api_password": "••••••••" if _setting("exefp_api_password", "") else "(not set)",
            "sign_in": "",
            "back": "",
        }

    def _render_settings(self) -> Group:
        values = self._settings_values()
        table = Table(box=None, expand=True, padding=(0, 1), show_header=False)
        table.add_column("", width=2)
        table.add_column("Setting", width=24, style=self.theme.text)
        table.add_column("Value", ratio=1, style=self.theme.muted)
        for index, (key, label) in enumerate(SETTINGS_ITEMS):
            selected = index == self.settings_index
            value = values.get(key, "")
            if self.editing and self.editing["key"] == key:
                value = self.editing["value"] + "▏"
            table.add_row(
                "❯" if selected else " ",
                label,
                escape(value),
                style=f"on {self.theme.selection}" if selected else "",
            )

        edit_hint = Text("")
        if self.editing:
            edit_hint = Text(
                f"Editing {self.editing['label']} — Enter saves, Esc cancels"
                + (" (input hidden)" if self.editing.get("secret") else ""),
                style=self.theme.accent,
            )

        return Group(
            self._header(),
            Text(""),
            Text("Settings", style=f"bold {self.theme.text}"),
            Text(""),
            table,
            Text(""),
            edit_hint,
            Text(self.settings_status, style=self.theme.muted),
            Text(""),
            self._footer(
                [("↑↓", "move"), ("Enter", "change"), ("Esc", "back"), ("Ctrl+C", "quit")]
            ),
        )

    # ── render dispatch ────────────────────────────────────────────────────
    def render(self) -> Group:
        if self.view == "search":
            return self._render_search()
        if self.view == "game":
            return self._render_game()
        if self.view == "scan":
            return self._render_scan()
        if self.view == "exes":
            return self._render_exes()
        if self.view == "download":
            return self._render_download()
        if self.view == "api_choice":
            return self._render_api_choice()
        if self.view == "settings":
            return self._render_settings()
        return self._render_search()

    # ── background events ──────────────────────────────────────────────────
    def _drain_events(self) -> None:
        while True:
            try:
                name, payload = self.events.get_nowait()
            except queue.Empty:
                return

            if name == "search_done":
                self.results, self.search_error = payload
                self.result_index = 0
                self.busy = False
                self.status_message = f"{len(self.results)} result(s)."
            elif name == "search_failed":
                self.results = []
                self.search_error = str(payload)
                self.busy = False
            elif name == "meta_done":
                self.game_info = payload or {}
                self.game_loading = False
                if (payload or {}).get("name"):
                    self.app_name = payload["name"]
            elif name == "scan_progress":
                message, step, total = payload
                self.scan_message = message
                self.scan_step = step
                self.scan_total = total
            elif name == "scan_done":
                self.scan = payload
                self.busy = False
                self.exe_checked = set(range(len(self.scan.entries)))
                self.exe_index = 0
                self.scan_errors = list(self.scan.errors)
                if self.scan.entries:
                    if self.api_mode:
                        self.view = "api_choice"
                        self.api_choice_index = 0
                    else:
                        self.view = "exes"
                    self.status_message = (
                        f"Found {len(self.scan.entries)} executable(s)."
                    )
                    if self.api_mode and self.auto_download:
                        self._start_download()
                else:
                    self.view = "game"
                    self.status_message = (
                        "No executables found. " + (self.scan.errors[0] if self.scan.errors else "")
                    )
            elif name == "scan_failed":
                self.busy = False
                self.view = "game"
                self.scan_errors = [str(payload)]
                self.status_message = f"Scan failed: {payload}"
            elif name == "download_progress":
                self.download_log.append(str(payload))
                self.download_log = self.download_log[-200:]
            elif name == "download_done":
                self.download_result = payload
                self.busy = False
            elif name == "login_done":
                ok, message = payload
                self.settings_status = message
                self.busy = False
            self.dirty = True

    # ── actions ────────────────────────────────────────────────────────────
    def _start_search(self) -> None:
        query = self.query.strip()
        if len(query) < 2:
            self.search_error = "Enter at least 2 characters."
            return
        self.search_error = ""
        self.busy = True
        self.status_message = "Searching…"
        self.searched_query = query
        self.dirty = True

        def worker():
            try:
                from core import unified_search

                results = unified_search.search(query)
                choices = [
                    AppChoice(
                        appid=int(entry["appid"]),
                        name=entry.get("name", ""),
                        sources=list(entry.get("sources") or []),
                    )
                    for entry in results
                    if entry.get("appid")
                ]
                self.events.put(("search_done", (choices, "")))
            except Exception as exc:
                self.events.put(("search_failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _open_game(self) -> None:
        if not self.results:
            return
        choice = self.results[min(self.result_index, len(self.results) - 1)]
        self.appid = choice.appid
        self.app_name = choice.name
        self.game_info = {}
        self.game_loading = True
        self.scan = None
        self.download_result = None
        self.download_log = []
        self.status_message = ""
        self.view = "game"
        self.dirty = True

        appid = choice.appid

        def worker():
            try:
                from core.steam_api import fetch_steamcmd_info

                self.events.put(("meta_done", fetch_steamcmd_info(str(appid))))
            except Exception:
                self.events.put(("meta_done", {}))

        threading.Thread(target=worker, daemon=True).start()

    def _start_scan(self) -> None:
        if not self.appid:
            return
        if self.scan:
            engine.cleanup_keys(self.scan)
        self.view = "scan"
        self.busy = True
        self.scan = None
        self.scan_errors = []
        self.scan_step = 0
        self.scan_total = 0
        self.scan_message = "Starting scan…"
        self.cancel_event = threading.Event()
        self.dirty = True

        def progress(message: str, step: int, total: int) -> None:
            self.events.put(("scan_progress", (message, step, total)))

        def worker():
            try:
                scan = engine.scan_app(
                    str(self.appid), progress=progress, cancel=self.cancel_event
                )
                self.events.put(("scan_done", scan))
            except Exception as exc:
                self.events.put(("scan_failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _start_download(self) -> None:
        if not self.scan or not self.appid:
            return
        chosen = [
            self.scan.entries[index]
            for index in sorted(self.exe_checked)
            if index < len(self.scan.entries)
        ]
        if not chosen:
            self.status_message = "Select at least one executable first."
            self.view = "exes"
            return

        self.view = "download"
        self.busy = True
        self.download_log = []
        self.download_result = None
        self.download_dir = engine.default_destination(str(self.appid), self.destination)
        self.cancel_event = threading.Event()
        self.dirty = True

        def progress(message: str) -> None:
            self.events.put(("download_progress", message))

        def worker():
            try:
                count, errors = engine.download_selected(
                    self.scan,
                    chosen,
                    destination=self.destination,
                    progress=progress,
                    cancel=self.cancel_event,
                )
                self.events.put(("download_done", (count, errors)))
            except Exception as exc:
                self.events.put(("download_done", (0, [str(exc)])))

        threading.Thread(target=worker, daemon=True).start()

    def _open_desktop(self) -> None:
        if not self.appid:
            return
        appid = str(self.appid)
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            cmd = [appimage, "--appid", appid]
        else:
            main_py = Path(__file__).resolve().parent.parent / "main.py"
            if main_py.exists():
                cmd = [sys.executable, str(main_py), "--appid", appid]
            else:
                self.status_message = "Could not find the desktop entrypoint."
                return
        try:
            subprocess.Popen(cmd, start_new_session=True)
            self.status_message = "Opened the desktop app."
        except OSError as exc:
            self.status_message = f"Could not open the desktop app: {exc}"

    def _sign_in(self) -> None:
        self.busy = True
        self.settings_status = "Signing in…"

        def worker():
            try:
                from core import exe_fingerprinter_api as api

                result = api.login()
                if result.ok:
                    data = result.data or {}
                    message = f"Signed in as {data.get('username', '')}."
                else:
                    message = result.error or "Sign-in failed."
                self.events.put(("login_done", (result.ok, message)))
            except Exception as exc:
                self.events.put(("login_done", (False, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    # ── settings handling ──────────────────────────────────────────────────
    def _edit_setting(self, key: str, label: str) -> None:
        if key == "theme":
            order = ["auto", "ocean", "rice", "minimal"]
            current = str(_setting("tui_theme", "auto") or "auto").lower()
            nxt = order[(order.index(current) + 1) % len(order)] if current in order else "auto"
            _set_setting("tui_theme", nxt)
            self.theme = resolve_theme(nxt)
            self.settings_status = f"Theme set to {nxt}."
        elif key == "colour_blind":
            value = not _bool_setting("colour_blind_mode")
            _set_setting("colour_blind_mode", value)
            self.theme = resolve_theme(self.theme_name)
            self.settings_status = f"Colour-blind mode {'on' if value else 'off'}."
        elif key == "sixel":
            value = not _bool_setting("tui_sixel", True)
            _set_setting("tui_sixel", value)
            self.settings_status = f"Sixel graphics {'on' if value else 'off'} (next launch)."
        elif key == "sfw":
            value = not _bool_setting("sfw_only", True)
            _set_setting("sfw_only", value)
            self.settings_status = f"SFW-only artwork {'on' if value else 'off'}."
        elif key in EDITABLE:
            current = str(_setting(
                {"api_base": "exefp_api_base", "api_user": "exefp_api_username", "api_password": "exefp_api_password"}[key],
                "",
            ) or "")
            self.editing = {
                "key": key,
                "label": label,
                "value": current,
                "secret": key == "api_password",
            }
        elif key == "sign_in":
            self._sign_in()
        elif key == "back":
            self.view = "game" if self.appid else "search"

    def _save_editing(self) -> None:
        if not self.editing:
            return
        key = self.editing["key"]
        value = self.editing["value"]
        setting_key = {
            "api_base": "exefp_api_base",
            "api_user": "exefp_api_username",
            "api_password": "exefp_api_password",
        }[key]
        _set_setting(setting_key, value)
        self.settings_status = f"Saved {self.editing['label']}."
        self.editing = None

    # ── key dispatch ───────────────────────────────────────────────────────
    def handle_key(self, key: str) -> None:
        self.dirty = True
        if key in ("ctrl_c", "ctrl_q"):
            self.running = False
            return

        if key == "f2":
            self.view = "settings"
            self.settings_index = 0
            self.editing = None
            return

        if self.editing:
            if key == "esc":
                self.editing = None
            elif key == "enter":
                self._save_editing()
            elif key == "backspace":
                self.editing["value"] = self.editing["value"][:-1]
            elif len(key) == 1 and key.isprintable():
                self.editing["value"] += key
            return

        if self.view == "search":
            if key == "enter":
                # The first Enter runs the search; once the shown results match
                # the query, the next Enter opens the highlighted game.
                if self.results and self.query.strip() == self.searched_query:
                    self._open_game()
                else:
                    self._start_search()
            elif key == "up":
                self.result_index = max(0, self.result_index - 1)
            elif key == "down":
                self.result_index = min(max(0, len(self.results) - 1), self.result_index + 1)
            elif key == "pageup":
                self.result_index = max(0, self.result_index - 10)
            elif key == "pagedown":
                self.result_index = min(max(0, len(self.results) - 1), self.result_index + 10)
            elif key == "backspace":
                self.query = self.query[:-1]
            elif key == "esc":
                self.query = ""
                self.results = []
                self.search_error = ""
            elif key == "space":
                self.query += " "
            elif len(key) == 1 and key.isprintable():
                self.query += key
                self.search_error = ""
            # Selecting a result with Enter would make searching and opening
            # the same key; Enter searches, Ctrl+Enter opens.
            return

        if self.view == "game":
            if key in ("esc",):
                self.view = "search"
            elif key in ("f", "F"):
                self._start_scan()
            elif key in ("e", "E"):
                if self.scan and self.scan.entries:
                    self.view = "exes"
                else:
                    self.status_message = "Run the fingerprint first (F)."
            elif key in ("o", "O"):
                self._open_desktop()
            return

        if self.view == "scan":
            if key == "esc":
                self.cancel_event.set()
                self.scan_message = "Cancelling…"
            return

        if self.view == "exes":
            entries = self.scan.entries if self.scan else []
            if key == "esc":
                self.view = "api_choice" if self.api_mode else "game"
            elif key == "up":
                self.exe_index = max(0, self.exe_index - 1)
            elif key == "down":
                self.exe_index = min(max(0, len(entries) - 1), self.exe_index + 1)
            elif key == "pageup":
                self.exe_index = max(0, self.exe_index - self._exe_visible_height())
            elif key == "pagedown":
                self.exe_index = min(
                    max(0, len(entries) - 1),
                    self.exe_index + self._exe_visible_height(),
                )
            elif key in ("space", "enter"):
                if self.exe_index in self.exe_checked:
                    self.exe_checked.remove(self.exe_index)
                else:
                    self.exe_checked.add(self.exe_index)
            elif key in ("a", "A"):
                self.exe_checked = set(range(len(entries)))
            elif key in ("n", "N"):
                self.exe_checked = set()
            elif key in ("d", "D"):
                self._start_download()
            elif key in ("o", "O"):
                self._open_desktop()
            return

        if self.view == "download":
            if key == "esc":
                if self.download_result is not None:
                    self.view = "exes"
                else:
                    self.cancel_event.set()
                    self.download_log.append("Cancelling…")
            return

        if self.view == "api_choice":
            if key == "up":
                self.api_choice_index = (self.api_choice_index - 1) % 2
            elif key == "down":
                self.api_choice_index = (self.api_choice_index + 1) % 2
            elif key == "enter":
                if self.api_choice_index == 0:
                    self.view = "exes"
                else:
                    self._open_desktop()
            elif key == "esc":
                self.view = "game"
            return

        if self.view == "settings":
            if key == "esc":
                self.view = "game" if self.appid else "search"
            elif key == "up":
                self.settings_index = (self.settings_index - 1) % len(SETTINGS_ITEMS)
            elif key == "down":
                self.settings_index = (self.settings_index + 1) % len(SETTINGS_ITEMS)
            elif key == "enter":
                item_key, label = SETTINGS_ITEMS[self.settings_index]
                self._edit_setting(item_key, label)
            return

    # ── main loop ──────────────────────────────────────────────────────────
    def run(self) -> int:
        from rich.live import Live

        if self.initial_appid and str(self.initial_appid).isdigit():
            self.appid = int(self.initial_appid)
            self.app_name = f"AppID {self.initial_appid}"
            self.view = "game"
            if self.api_mode:
                threading.Thread(
                    target=lambda: self.events.put(("meta_done", {})), daemon=True
                ).start()
                # Straight into the fingerprint: API mode is about the .exe list.
                self._start_scan()
            else:
                def worker():
                    try:
                        from core.steam_api import fetch_steamcmd_info

                        info = fetch_steamcmd_info(str(self.appid))
                        self.events.put(("meta_done", info))
                        self.app_name = info.get("name") or self.app_name
                    except Exception:
                        self.events.put(("meta_done", {}))

                threading.Thread(target=worker, daemon=True).start()

        with RawTerminal():
            self._maybe_sixel_splash()
            with Live(
                self.render(),
                console=self.console,
                screen=True,
                auto_refresh=False,
                transient=False,
            ) as live:
                while self.running:
                    self._drain_events()
                    if self.busy or self.dirty:
                        live.update(self.render(), refresh=True)
                        self.dirty = False
                    key = read_key(0.12)
                    if key:
                        self.handle_key(key)

        if self.scan:
            engine.cleanup_keys(self.scan)
        return 0


def _api_batch(argv: List[str]) -> Optional[int]:
    """Non-interactive ``--api --appid …`` path for scripts and CI."""
    if "--api" not in argv or "--appid" not in argv:
        return None
    if "--download" not in argv:
        # Fingerprint-only, no download: still fully headless.
        appid = None
        for index, arg in enumerate(argv):
            if arg == "--appid" and index + 1 < len(argv):
                appid = argv[index + 1]
            elif arg.startswith("--appid="):
                appid = arg.split("=", 1)[1]
        if not appid or not str(appid).isdigit():
            return None
        console = Console()
        console.print(f"[bold]Fingerprinting AppID {appid}[/bold]")
        scan = engine.scan_app(str(appid))
        if not scan.entries:
            console.print("[yellow]No executables found.[/yellow]")
            for error in scan.errors:
                console.print(f"[red]{escape(error)}[/red]")
            engine.cleanup_keys(scan)
            return 2
        console.print(f"Found [bold]{len(scan.entries)}[/bold] executable(s):")
        for entry in scan.entries:
            console.print(f"  {entry['depot_id']:>10}  {escape(entry['path'])}")
        engine.cleanup_keys(scan)
        return 0
    appid = None
    for index, arg in enumerate(argv):
        if arg == "--appid" and index + 1 < len(argv):
            appid = argv[index + 1]
        elif arg.startswith("--appid="):
            appid = arg.split("=", 1)[1]
    if not appid or not str(appid).isdigit():
        return None

    console = Console()
    console.print(f"[bold]Fingerprinting AppID {appid}[/bold]")
    scan = engine.scan_app(str(appid))
    if not scan.entries:
        console.print("[yellow]No executables found.[/yellow]")
        for error in scan.errors:
            console.print(f"[red]{escape(error)}[/red]")
        engine.cleanup_keys(scan)
        return 2

    console.print(f"Found [bold]{len(scan.entries)}[/bold] executable(s):")
    for entry in scan.entries:
        console.print(f"  {entry['depot_id']:>10}  {escape(entry['path'])}")
    console.print(f"Downloading to {engine.default_destination(str(appid))}")
    count, errors = engine.download_selected(
        scan, scan.entries, progress=lambda line: console.print(escape(line))
    )
    engine.cleanup_keys(scan)
    for error in errors:
        console.print(f"[red]{escape(error)}[/red]")
    console.print(f"[green]{count} depot(s) downloaded.[/green]")
    return 0 if not errors else 2


def run_tui(argv: Optional[List[str]] = None) -> int:
    """Entrypoint used by ``main.py --tui``."""
    argv = list(argv if argv is not None else sys.argv)
    batch = _api_batch(argv)
    if batch is not None:
        return batch
    if not sys.stdin.isatty():
        Console().print(
            "[bold]EXE-fingerprinter TUI[/bold] needs an interactive terminal.\n\n"
            "  [cyan]--tui[/cyan]                      search / fingerprint / download\n"
            "  [cyan]--api --appid ID[/cyan]           fingerprint only (two choices)\n"
            "  [cyan]--api --appid ID --download[/cyan] fingerprint and download every .exe"
        )
        return 1
    try:
        return FingerprinterTUI(argv).run()
    except KeyboardInterrupt:
        return 130


__all__ = ["FingerprinterTUI", "run_tui"]
