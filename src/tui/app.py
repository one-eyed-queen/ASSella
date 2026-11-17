"""EXE-fingerprinter — the Steam .exe downloader (TUI).

A faithful take on paws' interface: an app header with a banner, a menu-driven
home screen with art, and screens that follow the same shortcut conventions.
Keeps EXE-fingerprinter's own job: nothing is installed, nothing is registered
with Steam — you pick a game and download only its executables.

``--api --appid ID [--download]`` is the non-interactive path for CI and does
not need a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
import logging
from typing import Optional

from textual import events, work
from textual.app import App
from textual.binding import Binding
from textual.filter import NoColor
from textual.screen import ModalScreen
from textual.theme import Theme

from version import APP_VERSION

logger = logging.getLogger(__name__)

from . import settings
from .jobs import Job
from .underlay import Underlay

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAWS_THEME = Theme(
    name="paws",
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


class FingerprinterApp(App):
    TITLE = "EXE-fingerprinter"
    SUB_TITLE = f".exe downloader · v{APP_VERSION}"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
        Binding("f2", "settings", "settings"),
    ]

    def __init__(self, argv: Optional[list] = None):
        super().__init__()
        self.argv = list(argv if argv is not None else sys.argv)
        self.api_mode = "--api" in self.argv or "--api-mode" in self.argv
        self.register_theme(PAWS_THEME)
        self.theme = "paws"
        self.job = Job()
        self.underlay: Underlay | None = None
        self._plain = NoColor()
        self._filters.append(self._plain)
        self._set_look()

    def on_mount(self):
        self.underlay = Underlay(self)
        self.underlay.start()
        self.app_resume_signal.subscribe(self, lambda app: self.force_full_repaint())
        from .screens import HomeScreen

        self.push_screen(HomeScreen())
        self.run_worker(self._silent_sign_in, thread=True, group="auth-check", exclusive=True)

    @work(thread=True, exclusive=True, group="auth-check")
    def _silent_sign_in(self):
        """Sign in silently with the saved credentials — never prompts."""
        try:
            from core import exe_fingerprinter_api as api

            if not api.is_configured():
                return
            result = api.ensure_token()
            if not result.ok:
                logger.info("[Auth] silent sign-in failed: %s", result.error)
                self.call_from_thread(
                    self.notify,
                    f"API sign-in failed — check Settings ({result.error})",
                    title="EXE-fingerprinter",
                    severity="warning",
                    timeout=8,
                )
        except Exception as exc:
            logger.debug("[Auth] silent sign-in error: %s", exc)

    def _set_look(self):
        minimal = settings.is_minimal()
        self.set_class(minimal, "-minimal")
        self.theme = "minimal" if minimal else "paws"
        self._plain.enabled = minimal

    def run_job(self, label: str, work, finish=None) -> bool:
        """work(progress) runs in a thread; finish(result) runs after it on the UI thread."""
        if self.job.active:
            self.notify("busy", severity="warning")
            return False
        self.job.start(label)

        def body():
            try:
                return work(self.job)
            except Exception as exc:
                return exc

        async def after():
            result = await self.run_worker(body, thread=True, exclusive=True).wait()
            self.job.done()
            if isinstance(result, Exception):
                self.notify(str(result), severity="error")
            elif finish is not None:
                finish(result)

        self.run_worker(after, exclusive=False, group="job")
        return True

    def action_quit(self):
        if self.underlay is not None:
            self.underlay.stop()
        self.exit()

    def action_settings(self):
        from .screens import SettingsScreen

        self.push_screen(SettingsScreen())


def run_tui(argv: Optional[list] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    # Non-interactive CI path: fingerprint (and optionally download) without a TTY.
    batch = _api_batch(argv)
    if batch is not None:
        return batch

    _prime_image_probe()
    try:
        app = FingerprinterApp(argv)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"EXE-fingerprinter TUI failed to start: {exc}", file=sys.stderr)
        return 1


def _prime_image_probe() -> None:
    """Probe the terminal once, while stdin is still in canonical mode."""
    try:
        from textual_image import _terminal as probe

        probe._PROBE_TIMEOUT = 0.7
        probe.get_cell_size()
    except Exception:
        pass


def _api_batch(argv):
    """Non-interactive ``--api --appid …`` path for scripts and CI."""
    if "--api" not in argv or "--appid" not in argv:
        return None

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
    print(f"Fingerprinting {appid} — {app_name}")

    scan = engine.scan_app(str(appid))
    if not scan.entries:
        print("No executables found.")
        for error in scan.errors:
            print(f"  error: {error}")
        engine.cleanup_keys(scan)
        return 2

    print(f"Found {len(scan.entries)} executable(s):")
    for entry in scan.entries:
        print(f"  {entry['depot_id']:>10}  {entry['path']}")

    if not download:
        return 0

    dest = engine.default_destination(str(appid))
    print(f"Downloading to {dest}")
    count, errors = engine.download_selected(
        scan,
        scan.entries,
        destination=dest,
        progress=lambda line: print(f"  {line}"),
    )
    engine.cleanup_keys(scan)
    if errors:
        for error in errors:
            print(f"  error: {error}")
        return 2
    print(f"Done — {count} depot(s) downloaded.")
    return 0
