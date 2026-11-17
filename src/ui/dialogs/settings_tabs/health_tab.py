"""Health page: is this machine ready to download, and are the best settings on?

Three things live here, in the order a person debugs them:

1. **System status** - the DepotDownloader backend, the .NET runtime it runs
   on, and whether Steam is open. Each tile says what is wrong in the state
   itself; clicking one explains it.
2. **Steam updates** - the ``steam.cfg`` switch, so Steam cannot replace the
   client underneath a patched install.
3. **Recommended settings** - the handful of switches that make the download
   flow pleasant, with one button to turn them all on.
"""

import logging
import shutil
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QScrollArea,
    QSizePolicy,
)

from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.font_safety import status_dot
from utils.paths import Paths

logger = logging.getLogger(__name__)


class HealthStatusTile(QPushButton):
    """Clean interactive status tile button used in the Health tab dashboard."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel(title)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.65); border: none; background: transparent;")
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_lbl)

        self.status_lbl = QLabel("Checking...")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        self.status_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.status_lbl)

        self.set_state("neutral", "Checking...")

    def set_state(self, state: str, text: str):
        self.status_lbl.setText(text)
        if state == "ok":
            bg = "rgba(76, 175, 80, 0.12)"
            border = "rgba(76, 175, 80, 0.45)"
            color = "#81C784"
            hover_bg = "rgba(76, 175, 80, 0.22)"
        elif state == "warn":
            bg = "rgba(255, 152, 0, 0.12)"
            border = "rgba(255, 152, 0, 0.45)"
            color = "#FFB74D"
            hover_bg = "rgba(255, 152, 0, 0.22)"
        elif state == "error":
            bg = "rgba(244, 67, 54, 0.12)"
            border = "rgba(244, 67, 54, 0.45)"
            color = "#E57373"
            hover_bg = "rgba(244, 67, 54, 0.22)"
        else:
            bg = "rgba(255, 255, 255, 0.04)"
            border = "rgba(255, 255, 255, 0.15)"
            color = "rgba(255, 255, 255, 0.7)"
            hover_bg = "rgba(255, 255, 255, 0.08)"

        self.status_lbl.setStyleSheet(f"font-size: 10.5pt; font-weight: bold; color: {color}; border: none; background: transparent;")
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {color};
            }}
        """)


def create_rec_setting_row(title: str, desc: str, checkbox: QCheckBox) -> QWidget:
    """One recommended setting: a title and description beside its checkbox.

    The colours come from the surface the row is drawn on, so the row stays
    legible on a light background too. The checkbox is deliberately left
    unstyled: the application stylesheet already draws it (transparent
    indicator, no border), and a local override only fights that.
    """
    from utils.color_utils import get_raised_surface_tokens
    from utils.settings import get_settings

    settings = get_settings()
    surface = settings.value("background_color", "#111318", type=str) if settings else "#111318"
    tokens = get_raised_surface_tokens(surface)

    row = QWidget()
    row.setObjectName("rec_setting_row")
    row.setMinimumHeight(48)
    row.setStyleSheet(f"""
        QWidget#rec_setting_row {{
            background-color: {tokens["tint"]};
            border: 1px solid {tokens["border"]};
            border-radius: 8px;
        }}
        QWidget#rec_setting_row:hover {{
            background-color: {tokens["tint_hover"]};
            border-color: {tokens["border_hover"]};
        }}
    """)
    h = QHBoxLayout(row)
    h.setContentsMargins(14, 8, 14, 8)
    h.setSpacing(12)

    col = QVBoxLayout()
    col.setSpacing(2)

    t_lbl = QLabel(title)
    t_lbl.setStyleSheet(f"font-size: 9.5pt; font-weight: 600; color: {tokens['ink']}; border: none; background: transparent;")
    d_lbl = QLabel(desc)
    d_lbl.setStyleSheet(f"font-size: 8.2pt; color: {tokens['ink_muted']}; border: none; background: transparent;")
    d_lbl.setWordWrap(True)

    col.addWidget(t_lbl)
    col.addWidget(d_lbl)
    h.addLayout(col, 1)

    checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
    h.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def _secondary_button_style(accent: str) -> str:
    return f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {accent};
        }}
    """


def create_health_tab(dialog) -> QWidget:
    """Create the Health settings tab: system readiness, updates, recommendations."""
    tab = QWidget()
    tab.setStyleSheet("background: transparent;")
    dialog.health_tab = tab

    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
    if scroll.viewport():
        scroll.viewport().setStyleSheet("background: transparent;")

    container = QWidget()
    container.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    # ── 1. System status ─────────────────────────────────────────────────
    sys_card, sys_layout = dialog._create_card_frame("System Status")

    tiles_row = QHBoxLayout()
    tiles_row.setContentsMargins(0, 4, 0, 4)
    tiles_row.setSpacing(12)

    dialog.health_backend_btn = HealthStatusTile("Download Backend")
    dialog.health_backend_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    tiles_row.addWidget(dialog.health_backend_btn, 1)

    dialog.health_dotnet_btn = HealthStatusTile(".NET Runtime")
    dialog.health_dotnet_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    tiles_row.addWidget(dialog.health_dotnet_btn, 1)

    dialog.health_steam_btn = HealthStatusTile("Steam Client")
    dialog.health_steam_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    tiles_row.addWidget(dialog.health_steam_btn, 1)

    sys_layout.addLayout(tiles_row)

    path_row = QHBoxLayout()
    path_row.setContentsMargins(2, 6, 2, 2)
    path_row.setSpacing(10)

    dialog.health_sls_path_lbl = QLabel("Checking...")
    dialog.health_sls_path_lbl.setWordWrap(True)
    dialog.health_sls_path_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; font-family: monospace; border: none; background: transparent;")
    dialog.health_sls_path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    path_row.addWidget(dialog.health_sls_path_lbl, 1)

    dialog.health_refresh_btn = QPushButton("Refresh Status")
    dialog.health_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_refresh_btn.setStyleSheet(_secondary_button_style(dialog.accent_color))
    dialog.health_refresh_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    path_row.addWidget(dialog.health_refresh_btn, 0)

    sys_layout.addLayout(path_row)

    # Steam updates: the steam.cfg block, and the button that flips it.
    steam_updates_row = QHBoxLayout()
    steam_updates_row.setContentsMargins(2, 2, 2, 2)
    steam_updates_row.setSpacing(8)

    dialog.health_steam_updates_lbl = QLabel("")
    dialog.health_steam_updates_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
    dialog.health_steam_updates_lbl.setWordWrap(True)
    dialog.health_steam_updates_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    steam_updates_row.addWidget(dialog.health_steam_updates_lbl, 1)

    dialog.health_steam_updates_btn = QPushButton("Disable Steam Updates")
    dialog.health_steam_updates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_steam_updates_btn.setStyleSheet(_secondary_button_style(dialog.accent_color))
    dialog.health_steam_updates_btn.setToolTip("Steam reads steam.cfg when it starts, so this applies on the next Steam start.")
    dialog.health_steam_updates_btn.clicked.connect(lambda: on_steam_updates_clicked(dialog))
    steam_updates_row.addWidget(dialog.health_steam_updates_btn, 0)

    sys_layout.addLayout(steam_updates_row)
    refresh_steam_updates_control(dialog)
    layout.addWidget(sys_card)

    # ── 2. Recommended Settings ──────────────────────────────────────────
    rec_card, rec_layout = dialog._create_card_frame("")
    rec_header_row = QHBoxLayout()
    rec_header_row.setContentsMargins(0, 0, 0, 2)
    rec_title_lbl = QLabel("Recommended Settings")
    rec_title_lbl.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {dialog.accent_color}; border: none; background: transparent;")
    rec_header_row.addWidget(rec_title_lbl)
    rec_header_row.addStretch()

    dialog.rec_score_badge = QLabel(f"{status_dot()} Checking...")
    dialog.rec_score_badge.setStyleSheet("font-size: 8.5pt; font-weight: bold; color: #81C784; background: rgba(76, 175, 80, 0.12); border: 1px solid rgba(76, 175, 80, 0.35); border-radius: 6px; padding: 2px 10px;")
    rec_header_row.addWidget(dialog.rec_score_badge)
    rec_layout.addLayout(rec_header_row)

    rec_desc = QLabel("The settings that keep the download flow predictable and quick.")
    rec_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent; margin-bottom: 4px;")
    rec_desc.setWordWrap(True)
    rec_layout.addWidget(rec_desc)

    dialog.twp_chk_gateway = QCheckBox()
    dialog.twp_chk_gateway.setChecked(dialog.settings.value("isp_bypass_mode", "auto", type=str) == "auto")
    dialog.twp_chk_gateway.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Hubcap Gateway (Auto)",
        "Smart fallback routing to bypass ISP throttling and rate limits",
        dialog.twp_chk_gateway,
    ))

    dialog.twp_chk_achievements = QCheckBox()
    dialog.twp_chk_achievements.setChecked(not dialog.settings.value("generate_achievements", True, type=bool))
    dialog.twp_chk_achievements.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Skip achievement generation",
        "Skip legacy achievement schema generation to accelerate downloads",
        dialog.twp_chk_achievements,
    ))

    dialog.twp_chk_clutter = QCheckBox()
    dialog.twp_chk_clutter.setChecked(
        dialog.settings.value("hide_macos_depots", False, type=bool)
        and dialog.settings.value("hide_android_depots", False, type=bool)
    )
    dialog.twp_chk_clutter.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Hide platform clutter",
        "Filter out unnecessary macOS & Android depots from download queues",
        dialog.twp_chk_clutter,
    ))

    dialog.health_apply_rec_btn = QPushButton("Apply Recommended Settings")
    dialog.health_apply_rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_apply_rec_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.health_apply_rec_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {dialog.accent_color};
            color: #000000;
            border: none;
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 9.5pt;
            font-weight: bold;
            margin-top: 6px;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: rgba(255, 255, 255, 0.28) !important;
        }}
    """)
    dialog.health_apply_rec_btn.clicked.connect(lambda: apply_health_recommended_settings(dialog))
    rec_layout.addWidget(dialog.health_apply_rec_btn)

    layout.addWidget(rec_card)
    layout.addStretch()
    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    dialog.tab_widget.addTab(tab, "Health")
    QTimer.singleShot(50, lambda: refresh_health_tab_status(dialog))
    update_rec_score_badge(dialog)
    return tab


# ── system status ────────────────────────────────────────────────────────

def _dotnet_status() -> tuple:
    """Return ``(state, label, detail)`` for the .NET runtime.

    DepotDownloader targets net9.0 with ``rollForward: LatestMajor``, so a 9
    or newer runtime is what matters. A missing CLI is a warning, not an
    error: packaged builds may use a bundled runtime.
    """
    exe = shutil.which("dotnet")
    if not exe:
        return "warn", "Not on PATH", "The dotnet command was not found. A packaged build may still ship its own runtime."
    try:
        result = subprocess.run(
            [exe, "--list-runtimes"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as e:
        logger.debug("[Health] dotnet --list-runtimes failed: %s", e)
        return "warn", "Unreadable", f"Could not ask {exe} for its runtimes: {e}"

    versions = []
    for line in result.stdout.splitlines():
        if line.startswith("Microsoft.NETCore.App "):
            parts = line.split()
            if len(parts) >= 2:
                versions.append(parts[1])
    if not versions:
        return "warn", "None installed", f"{exe} lists no Microsoft.NETCore.App runtime."

    majors = []
    for version in versions:
        try:
            majors.append(int(version.split(".")[0]))
        except ValueError:
            continue
    newest = sorted(versions, key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])[-1]
    if any(major >= 9 for major in majors):
        return "ok", newest, f"{exe} provides Microsoft.NETCore.App {newest}."
    return "warn", newest, f"Only .NET {newest} is installed; DepotDownloader needs 9 or newer."


def refresh_health_tab_status(dialog) -> None:
    """Refresh backend, runtime and Steam state, then say what is wrong."""
    if not hasattr(dialog, "health_backend_btn") or not dialog.health_backend_btn:
        return

    dll = Paths.depot_downloader_dll()
    backend_ok = dll.exists()
    dialog.health_backend_btn.set_state(
        "ok" if backend_ok else "error",
        "Ready" if backend_ok else "Missing",
    )
    dialog.health_backend_btn.setToolTip(
        f"DepotDownloaderMod backend:\n{dll}\n\n"
        + ("Click to re-check." if backend_ok else "Reinstall EXE-fingerprinter; the download backend is part of the app.")
    )

    runtime_state, runtime_label, runtime_detail = _dotnet_status()
    dialog.health_dotnet_btn.set_state(runtime_state, runtime_label)
    dialog.health_dotnet_btn.setToolTip(runtime_detail)

    steam_running = False
    try:
        from core.steam_helpers import is_steam_running
        steam_running = is_steam_running()
    except Exception as e:
        logger.debug("[Health] Steam detection failed: %s", e)
    dialog.health_steam_btn.set_state(
        "ok" if steam_running else "warn",
        "Running" if steam_running else "Closed",
    )
    dialog.health_steam_btn.setToolTip(
        "Steam is open. Downloads work either way; installing into a Steam library needs Steam closed at the right moment."
        if steam_running else
        "Steam is not running. Downloads work fine; open Steam when you are ready to play."
    )

    if hasattr(dialog, "health_sls_path_lbl") and dialog.health_sls_path_lbl:
        dialog.health_sls_path_lbl.setText(f"Backend: {dll}")

    refresh_steam_updates_control(dialog)


# ── recommended settings ─────────────────────────────────────────────────

def on_rec_setting_toggled(dialog) -> None:
    update_rec_score_badge(dialog)


def update_rec_score_badge(dialog) -> None:
    if not hasattr(dialog, "rec_score_badge") or not dialog.rec_score_badge:
        return
    checks = [
        getattr(dialog, "twp_chk_gateway", None),
        getattr(dialog, "twp_chk_achievements", None),
        getattr(dialog, "twp_chk_clutter", None),
    ]
    active = sum(1 for check in checks if check and check.isChecked())
    total = len(checks)
    if active == total:
        dialog.rec_score_badge.setText(f"{status_dot()} {active}/{total} Optimal")
        dialog.rec_score_badge.setStyleSheet(
            "font-size: 8.5pt; font-weight: bold; color: #81C784; "
            "background: rgba(76, 175, 80, 0.12); "
            "border: 1px solid rgba(76, 175, 80, 0.35); "
            "border-radius: 6px; padding: 2px 10px;"
        )
        if hasattr(dialog, "health_apply_rec_btn") and dialog.health_apply_rec_btn:
            dialog.health_apply_rec_btn.setEnabled(False)
            dialog.health_apply_rec_btn.setToolTip("All recommended settings are already applied.")
    else:
        dialog.rec_score_badge.setText(f"{status_dot()} {active}/{total} applied")
        dialog.rec_score_badge.setStyleSheet(
            "font-size: 8.5pt; font-weight: bold; color: #FFB74D; "
            "background: rgba(255, 152, 0, 0.12); "
            "border: 1px solid rgba(255, 152, 0, 0.35); "
            "border-radius: 6px; padding: 2px 10px;"
        )
        if hasattr(dialog, "health_apply_rec_btn") and dialog.health_apply_rec_btn:
            dialog.health_apply_rec_btn.setEnabled(True)
            dialog.health_apply_rec_btn.setToolTip("Turn on the settings below.")


def apply_health_recommended_settings(dialog) -> None:
    if getattr(dialog, "twp_chk_gateway", None):
        dialog.twp_chk_gateway.setChecked(True)
    if getattr(dialog, "twp_chk_achievements", None):
        dialog.twp_chk_achievements.setChecked(True)
    if getattr(dialog, "twp_chk_clutter", None):
        dialog.twp_chk_clutter.setChecked(True)

    dialog.settings.setValue("smart_depot_selection", True)
    dialog.settings.setValue("isp_bypass_mode", "auto")
    dialog.settings.setValue("isp_bypass_hubcap", True)
    dialog.settings.setValue("generate_achievements", False)
    dialog.settings.setValue("hide_macos_depots", True)
    dialog.settings.setValue("hide_android_depots", True)
    dialog.settings.sync()

    if hasattr(dialog, "isp_gateway_combo") and dialog.isp_gateway_combo:
        idx = dialog.isp_gateway_combo.findData("auto")
        if idx >= 0:
            dialog.isp_gateway_combo.setCurrentIndex(idx)
    if getattr(dialog, "achievements_checkbox", None):
        dialog.achievements_checkbox.setChecked(False)
    if getattr(dialog, "hide_macos_depots_checkbox", None):
        dialog.hide_macos_depots_checkbox.setChecked(True)
    if getattr(dialog, "hide_android_depots_checkbox", None):
        dialog.hide_android_depots_checkbox.setChecked(True)

    update_rec_score_badge(dialog)
    ThemedMessageBox.information(dialog, "Settings Applied", "Recommended workflow settings applied successfully!")


# ── Steam updates (steam.cfg) ────────────────────────────────────────────

def refresh_steam_updates_control(dialog) -> None:
    """Show the live state of the steam.cfg update block in the Health tab."""
    btn = getattr(dialog, "health_steam_updates_btn", None)
    lbl = getattr(dialog, "health_steam_updates_lbl", None)
    if btn is None:
        return

    from utils.steam_updates import are_steam_updates_blocked, find_steam_cfg, target_steam_cfg

    blocked = are_steam_updates_blocked()
    existing = find_steam_cfg()
    path = existing if existing is not None else target_steam_cfg()

    btn.setText("Enable Steam Updates" if blocked else "Disable Steam Updates")

    if lbl is not None:
        lbl.setText("Steam needs a restart after changes.")

    btn.setToolTip(
        f"Toggles {path}\n"
        "Steam reads steam.cfg when it starts, so the change applies on the next Steam start."
    )


def on_steam_updates_clicked(dialog) -> None:
    """Create or remove steam.cfg, so Steam can (or cannot) update itself."""
    from utils.steam_updates import are_steam_updates_blocked, set_steam_updates_blocked

    blocked = are_steam_updates_blocked()

    if blocked:
        reply = ThemedMessageBox.question(
            dialog,
            "Enable Steam Updates",
            "Letting Steam update itself can replace the client files a patched install "
            "depends on, and the patch may stop working until it is reinstalled.\n\n"
            "Are you sure you want to enable Steam updates?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.No,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

    ok, message = set_steam_updates_blocked(not blocked)

    refresh_steam_updates_control(dialog)
    _refresh_main_window_status(dialog)

    if not ok:
        ThemedMessageBox.critical(dialog, "Steam Updates", message)
        return

    ThemedMessageBox.information(
        dialog,
        "Steam Updates",
        message + "\n\nSteam reads steam.cfg when it starts - restart Steam for this to take effect.",
    )


def _refresh_main_window_status(dialog) -> None:
    """Let the status visor show the new Steam Updates state right away."""
    main_window = getattr(dialog, "main_window", None)
    if main_window is None or not hasattr(main_window, "refresh_system_status"):
        return
    try:
        main_window.refresh_system_status()
    except Exception as e:
        logger.warning("Could not refresh the main window status: %s", e)
