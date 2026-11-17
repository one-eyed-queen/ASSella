import logging
import os
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QSlider,
    QSizePolicy,
    QScrollArea,
    QLineEdit,
)
from ui.widgets.modern_dropdown import ModernComboBox
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)


def create_exe_fingerprinter_tab(dialog) -> QWidget:
    """Create the EXE-fingerprinter settings tab."""
    tab = QWidget()
    tab.setStyleSheet("background: transparent;")
    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
    if scroll.viewport():
        scroll.viewport().setStyleSheet("background: transparent;")

    container = QWidget()
    container.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    # Section 1 Card: EXE-fingerprinter Settings
    exe_fingerprinter_card, exe_fingerprinter_lay = dialog._create_card_frame("EXE-fingerprinter Settings")
    if dialog.settings.value("smart_depot_selection") is None:
        dialog.settings.setValue("smart_depot_selection", True)

    # 1. Check Updates on Boot
    dialog.check_updates_on_boot_checkbox = create_checkbox_setting(
        "Check Updates on Boot",
        "check_updates_on_boot",
        True,
        dialog,
        "Automatically check for game updates in the background on startup.",
    )
    exe_fingerprinter_lay.addWidget(dialog.check_updates_on_boot_checkbox)

    # 1b. Update Channel Selector
    channel_row = QHBoxLayout()
    channel_row.setContentsMargins(0, 2, 0, 2)
    channel_lbl = QLabel("Update Channel:")
    channel_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    channel_lbl.setToolTip("Select the EXE-fingerprinter release channel: Stable (official releases), Beta (preview channel), or Canary (experimental).")
    channel_row.addWidget(channel_lbl)
    channel_row.addStretch()

    dialog.update_channel_combo = ModernComboBox()
    dialog.update_channel_combo.addItem("Stable", "stable")
    dialog.update_channel_combo.addItem("Beta", "beta")
    dialog.update_channel_combo.addItem("Canary", "canary")
    dialog.update_channel_combo.setFixedWidth(120)

    saved_channel = dialog.settings.value("update_channel", "stable", type=str).lower()
    c_idx = dialog.update_channel_combo.findData(saved_channel)
    if c_idx >= 0:
        dialog.update_channel_combo.setCurrentIndex(c_idx)
    else:
        dialog.update_channel_combo.setCurrentIndex(0)

    def _trigger_tool_update_recheck():
        mw = getattr(dialog, "main_window", None)
        if mw and hasattr(mw, "check_tool_updates"):
            try:
                mw.check_tool_updates(force=True)
            except TypeError:
                mw.check_tool_updates()

    def _on_channel_changed(idx):
        chosen = dialog.update_channel_combo.itemData(idx)
        dialog.settings.setValue("update_channel", chosen)
        dialog.settings.sync()
        mw = getattr(dialog, "main_window", None)
        if mw is not None:
            mw._latest_remote_version = ""
            mw._latest_update_source = ""
            mw._latest_update_channel = chosen or ""
            mw._tool_update_available_flag = False
            if hasattr(mw, "_update_tool_update_visibility"):
                mw._update_tool_update_visibility()
        _trigger_tool_update_recheck()

    dialog.update_channel_combo.currentIndexChanged.connect(_on_channel_changed)
    channel_row.addWidget(dialog.update_channel_combo)
    exe_fingerprinter_lay.addLayout(channel_row)

    # 1c. Update Server Selector
    server_row = QHBoxLayout()
    server_row.setContentsMargins(0, 2, 0, 2)
    server_lbl = QLabel("Update Server:")
    server_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    server_lbl.setToolTip(
        "Choose where EXE-fingerprinter checks for and downloads app updates: Auto (GitHub first, R2 fallback on errors), GitHub only, or R2 only."
    )
    server_row.addWidget(server_lbl)
    server_row.addStretch()

    dialog.update_server_combo = ModernComboBox()
    dialog.update_server_combo.addItem("Auto", "auto")
    dialog.update_server_combo.addItem("GitHub", "github")
    dialog.update_server_combo.addItem("R2", "r2")
    dialog.update_server_combo.setFixedWidth(120)

    saved_server = dialog.settings.value("update_server", "auto", type=str).strip().lower()
    s_idx = dialog.update_server_combo.findData(saved_server)
    dialog.update_server_combo.setCurrentIndex(s_idx if s_idx >= 0 else 0)

    def _on_server_changed(idx):
        chosen = dialog.update_server_combo.itemData(idx) or "auto"
        dialog.settings.setValue("update_server", chosen)
        dialog.settings.sync()
        _trigger_tool_update_recheck()

    dialog.update_server_combo.currentIndexChanged.connect(_on_server_changed)
    server_row.addWidget(dialog.update_server_combo)
    exe_fingerprinter_lay.addLayout(server_row)

    if dialog.settings.value("use_experimental_ddm") is None:
        dialog.settings.setValue("use_experimental_ddm", True)


    # 6. Title Bar Search Widget
    dialog.title_bar_search_checkbox = create_checkbox_setting(
        "Search Widget in Title Bar",
        "title_bar_search_enabled",
        False,
        dialog,
        "Show a live search bar and quick-results dropdown directly in the top title bar instead of the search button.",
    )

    def _on_search_widget_toggled(state):
        is_checked = (state == Qt.CheckState.Checked.value or state is True)
        dialog.settings.setValue("title_bar_search_enabled", is_checked)
        if hasattr(dialog, "main_window") and dialog.main_window:
            titlebar = getattr(dialog.main_window, "bottom_titlebar", None)
            if titlebar and hasattr(titlebar, "refresh_search_mode"):
                titlebar.refresh_search_mode()

    dialog.title_bar_search_checkbox.stateChanged.connect(_on_search_widget_toggled)
    exe_fingerprinter_lay.addWidget(dialog.title_bar_search_checkbox)

    # 4. Download Animation (Test/Halloween styles)
    anim_row = QHBoxLayout()
    anim_row.setContentsMargins(0, 2, 0, 2)
    anim_lbl = QLabel("Download Animation:")
    anim_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    anim_lbl.setToolTip("Choose the animation style rendered on the download progress bar (Standard, Candy Corn, Jack-o'-Lantern, or Fluttering Bat).")
    anim_row.addWidget(anim_lbl)
    anim_row.addStretch()

    dialog.download_animation_combo = ModernComboBox()
    dialog.download_animation_combo.addItem("Standard", "standard")
    dialog.download_animation_combo.addItem("Retro Ghost & Fog", "ghost")
    dialog.download_animation_combo.addItem("Pac-Man & Dots", "pacman")
    dialog.download_animation_combo.addItem("Flying Witch & Stardust", "witch")
    dialog.download_animation_combo.addItem("Goku (Kamehameha)", "goku")
    dialog.download_animation_combo.addItem("Sonic (Green Hill)", "sonic")
    dialog.download_animation_combo.addItem("Super Mario (Coins)", "mario")
    dialog.download_animation_combo.addItem("Shinobi Duel (Ninja Clash)", "shinobi")
    dialog.download_animation_combo.addItem("Nyan Cat (Rainbow)", "nyancat")
    dialog.download_animation_combo.addItem("Mega Man (Buster)", "megaman")
    dialog.download_animation_combo.addItem("Pokémon (Pokéball)", "pokemon")
    dialog.download_animation_combo.addItem("City Drive (Retro Car)", "car")
    dialog.download_animation_combo.addItem("Jack-o'-Lantern", "lantern")
    dialog.download_animation_combo.addItem("Santa & Snowy Village", "santa")
    dialog.download_animation_combo.setFixedWidth(220)

    saved_anim = dialog.settings.value("download_animation", "standard", type=str)
    idx_anim = dialog.download_animation_combo.findData(saved_anim)
    if idx_anim >= 0:
        dialog.download_animation_combo.setCurrentIndex(idx_anim)
    else:
        dialog.download_animation_combo.setCurrentIndex(0)

    anim_row.addWidget(dialog.download_animation_combo)
    exe_fingerprinter_lay.addLayout(anim_row)

    # 4. Update Check Interval Slider
    slider_layout = QHBoxLayout()
    slider_layout.setContentsMargins(0, 6, 0, 6)
    slider_label = QLabel("Update Check Interval:")
    slider_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    slider_label.setToolTip("Set how often to check for game updates. Move to the leftmost position (0) to disable.")

    dialog.update_interval_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.update_interval_slider.setRange(0, 20)
    dialog.update_interval_slider.setMinimumHeight(24)

    dialog.update_interval_slider.setStyleSheet("""
        QSlider {
            min-height: 24px;
        }
        QSlider::groove:horizontal {
            border: none;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: %s;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: %s;
            border: none;
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
        }
        QSlider::handle:horizontal:hover {
            background: white;
        }
    """ % (dialog.accent_color, dialog.accent_color))

    dialog.update_interval_value_label = QLabel("Disabled")
    dialog.update_interval_value_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
    dialog.update_interval_value_label.setFixedWidth(75)

    current_minutes = dialog.settings.value("update_check_interval_minutes", 0, type=int)
    slider_val = min(20, max(0, current_minutes // 5))
    dialog.update_interval_slider.setValue(slider_val)

    def update_slider_label(val):
        if val == 0:
            dialog.update_interval_value_label.setText("Disabled")
        else:
            dialog.update_interval_value_label.setText(f"{val * 5} mins")

    update_slider_label(slider_val)
    dialog.update_interval_slider.valueChanged.connect(update_slider_label)

    slider_layout.addWidget(slider_label)
    slider_layout.addWidget(dialog.update_interval_slider, 1)
    slider_layout.addWidget(dialog.update_interval_value_label)
    exe_fingerprinter_lay.addLayout(slider_layout)

    # SteamAPI provider selector
    provider_layout = QHBoxLayout()
    provider_layout.setSpacing(12)
    provider_label = QLabel("SteamAPI Provider:")
    provider_label.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
    provider_label.setToolTip(
        "Select which API service is prioritized for library game update checks.\n\n"
        "• SteamPICS: Queries Valve servers directly. Authoritative, live data with zero stale caching.\n"
        "• SteamcmdAPI: Fast parallel scan via CDN mirror."
    )

    dialog.update_provider_combo = ModernComboBox()
    dialog.update_provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    dialog.update_provider_combo.addItem("Auto (Hybrid)", "auto")
    dialog.update_provider_combo.addItem("SteamPICS", "steampics")
    dialog.update_provider_combo.addItem("SteamcmdAPI", "steamcmd")
    dialog.update_provider_combo.setToolTip(
        "Auto (Hybrid): Queries SteamCMD and live Steam PICS in tandem with highest-build resolution for maximum speed and zero stale data.\n"
        "SteamPICS: Live connection directly to Valve CM servers.\n"
        "SteamcmdAPI: Stateless HTTP requests via CDN."
    )

    current_provider = dialog.settings.value("update_check_api_provider", "auto", type=str)
    p_idx = dialog.update_provider_combo.findData(current_provider)
    if p_idx >= 0:
        dialog.update_provider_combo.setCurrentIndex(p_idx)

    provider_layout.addWidget(provider_label)
    provider_layout.addStretch(1)
    provider_layout.addWidget(dialog.update_provider_combo)
    exe_fingerprinter_lay.addLayout(provider_layout)

    # SLSah manual cheevos (Beta)
    dialog.slsah_cheevos_checkbox = create_checkbox_setting(
        "SLSah manual cheevos (Beta)",
        "enable_slsah_cheevos",
        False,
        dialog,
        "Enables viewing and manually managing achievements in the Game Details dialog.\n"
        "When disabled, the achievements tab is hidden and background cache checks are skipped.",
        show_description=False,
    )
    exe_fingerprinter_lay.addWidget(dialog.slsah_cheevos_checkbox)

    layout.addWidget(exe_fingerprinter_card)

    # ── Private API & Appearance ──────────────────────────────────────────
    api_card, api_lay = dialog._create_card_frame("Private API & Appearance")

    api_help = QLabel(
        "Sign in to the EXE-fingerprinter API to use its depot-key cache and "
        "fingerprint service. The account is locked to this device on first sign-in."
    )
    api_help.setWordWrap(True)
    api_help.setStyleSheet(
        "color: rgba(255,255,255,0.62); font-size: 8.5pt; border: none; background: transparent;"
    )
    api_lay.addWidget(api_help)

    def _api_edit_row(label_text: str, setting_key: str, placeholder: str, secret: bool = False):
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        label = QLabel(label_text)
        label.setFixedWidth(110)
        label.setStyleSheet(
            "color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;"
        )
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setText(dialog.settings.value(setting_key, "", type=str) or "")
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.14);"
            " border-radius: 5px; color: #FFFFFF; padding: 4px 8px; font-size: 9pt; }"
            "QLineEdit:focus { border-color: %s; }" % dialog.accent_color
        )

        def _save(_text=None, key=setting_key, widget=edit):
            dialog.settings.setValue(key, widget.text().strip())
            dialog.settings.sync()

        edit.editingFinished.connect(_save)
        row.addWidget(label)
        row.addWidget(edit, 1)
        api_lay.addLayout(row)
        return edit

    _api_base_edit = _api_edit_row(
        "API base URL", "exefp_api_base", "https://exe-fingerprinter.vercel.app"
    )
    _api_user_edit = _api_edit_row("API username", "exefp_api_username", "username")
    _api_pass_edit = _api_edit_row("API password", "exefp_api_password", "password", secret=True)

    sign_row = QHBoxLayout()
    sign_row.setContentsMargins(0, 4, 0, 2)
    sign_btn = QPushButton("Sign in")
    sign_btn.setFixedHeight(28)
    sign_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sign_btn.setStyleSheet(
        "QPushButton { background-color: %s; color: #000000; font-weight: 600; border: none;"
        " border-radius: 5px; padding: 4px 14px; font-size: 8.5pt; }"
        "QPushButton:hover { border: 1px solid #FFFFFF; }" % dialog.accent_color
    )
    api_status = QLabel("")
    api_status.setWordWrap(True)
    api_status.setStyleSheet(
        "color: rgba(255,255,255,0.65); font-size: 8.5pt; border: none; background: transparent;"
    )
    sign_row.addWidget(sign_btn)
    sign_row.addWidget(api_status, 1)
    api_lay.addLayout(sign_row)

    if not hasattr(dialog, "api_signin_runner"):
        from utils.task_runner import TaskRunner

        dialog.api_signin_runner = TaskRunner(dialog)

    def _on_signin_finished(result):
        if getattr(result, "ok", False):
            data = result.data or {}
            api_status.setStyleSheet(
                "color: #7ee787; font-size: 8.5pt; border: none; background: transparent;"
            )
            api_status.setText(f"Signed in as {data.get('username', '')}.")
        else:
            api_status.setStyleSheet(
                "color: #ff7b72; font-size: 8.5pt; border: none; background: transparent;"
            )
            api_status.setText(getattr(result, "error", "Sign-in failed."))

    def _on_signin():
        _api_base_edit.editingFinished.emit()
        _api_user_edit.editingFinished.emit()
        _api_pass_edit.editingFinished.emit()
        from core import exe_fingerprinter_api as api

        api_status.setStyleSheet(
            "color: rgba(255,255,255,0.65); font-size: 8.5pt; border: none; background: transparent;"
        )
        api_status.setText("Signing in…")
        sign_btn.setEnabled(False)
        dialog.api_signin_runner.run(
            api.login,
            on_finished=_on_signin_finished,
            on_completed=lambda: sign_btn.setEnabled(True),
        )

    sign_btn.clicked.connect(_on_signin)

    dialog.colour_blind_checkbox = create_checkbox_setting(
        "Colour-blind mode",
        "colour_blind_mode",
        False,
        dialog,
        "Replace red/green status colours with a blue/orange pair in the TUI and "
        "newly opened windows.",
        show_description=False,
    )
    api_lay.addWidget(dialog.colour_blind_checkbox)

    tui_row = QHBoxLayout()
    tui_row.setContentsMargins(0, 2, 0, 2)
    tui_label = QLabel("TUI theme:")
    tui_label.setStyleSheet(
        "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
    )
    dialog.tui_theme_combo = ModernComboBox()
    dialog.tui_theme_combo.addItem("Auto", "auto")
    dialog.tui_theme_combo.addItem("Ocean", "ocean")
    dialog.tui_theme_combo.addItem("Rice", "rice")
    dialog.tui_theme_combo.addItem("Minimal", "minimal")
    dialog.tui_theme_combo.setFixedWidth(140)
    saved_tui_theme = (dialog.settings.value("tui_theme", "auto", type=str) or "auto").lower()
    theme_index = dialog.tui_theme_combo.findData(saved_tui_theme)
    dialog.tui_theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)

    def _on_tui_theme_changed(index):
        dialog.settings.setValue("tui_theme", dialog.tui_theme_combo.itemData(index) or "auto")
        dialog.settings.sync()

    dialog.tui_theme_combo.currentIndexChanged.connect(_on_tui_theme_changed)
    tui_row.addWidget(tui_label)
    tui_row.addStretch(1)
    tui_row.addWidget(dialog.tui_theme_combo)
    api_lay.addLayout(tui_row)

    dialog.sixel_checkbox = create_checkbox_setting(
        "Sixel graphics in the TUI",
        "tui_sixel",
        True,
        dialog,
        "Draw the logo with sixel graphics where the terminal supports them. "
        "Falls back to text automatically.",
        show_description=False,
    )
    api_lay.addWidget(dialog.sixel_checkbox)

    dialog.sfw_checkbox = create_checkbox_setting(
        "SFW-only artwork",
        "sfw_only",
        True,
        dialog,
        "Only use safe-for-work artwork and metadata in the interface.",
        show_description=False,
    )
    api_lay.addWidget(dialog.sfw_checkbox)

    dialog.tui_launch_btn = QPushButton("Open the TUI in a terminal")
    dialog.tui_launch_btn.setFixedHeight(28)
    dialog.tui_launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.tui_launch_btn.setStyleSheet(
        "QPushButton { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.16);"
        " border-radius: 5px; color: #FFFFFF; padding: 4px 14px; font-size: 8.5pt; }"
        "QPushButton:hover { background: rgba(255,255,255,0.10); }"
    )

    def _launch_tui():
        from managers.cli_manager import open_tui_terminal

        if not open_tui_terminal():
            ThemedMessageBox.information(
                dialog,
                "TUI",
                "No terminal emulator was found.\n\nOpen a terminal and run:\n\nexe_fingerprinter --tui",
            )

    dialog.tui_launch_btn.clicked.connect(_launch_tui)
    api_lay.addWidget(dialog.tui_launch_btn)

    layout.addWidget(api_card)
    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    dialog.tab_widget.addTab(tab, "EXE-fingerprinter")
    return tab


from ui.dialogs.settings_tabs.morrenus_tab import (
    on_isp_gateway_changed,
    test_single_gateway,
    handle_gateway_test_done,
)


def uninstall_exe_fingerprinter(dialog) -> None:
    """Remove EXE-fingerprinter and optionally restore the original ACCELA backup."""
    install_dir = os.path.expanduser("~/.local/share/ACCELA")
    exe_fingerprinter_path = os.path.join(install_dir, "ExeFingerprinter.AppImage")
    symlink_path = os.path.join(install_dir, "ACCELA.AppImage")
    backup_path = os.path.join(install_dir, "ACCELA.AppImage.bak")
    desktop_entry = os.path.expanduser("~/.local/share/applications/accela.desktop")
    has_backup = os.path.isfile(backup_path)

    reply = ThemedMessageBox.question(
        dialog,
        "Uninstall EXE-fingerprinter",
        "This will remove EXE-fingerprinter and revert the desktop shortcut to ACCELA.\n\nAre you sure?",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
    )
    if reply != ThemedMessageBox.StandardButton.Yes:
        return

    restore = False
    if has_backup:
        restore_reply = ThemedMessageBox.question(
            dialog,
            "Restore Original ACCELA?",
            "A backup of the original ACCELA (ACCELA.AppImage.bak) was found.\n\n"
            "Would you like to restore it after uninstalling EXE-fingerprinter?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        )
        restore = restore_reply == ThemedMessageBox.StandardButton.Yes

    errors = []

    if os.path.islink(symlink_path):
        try:
            os.remove(symlink_path)
        except OSError as e:
            errors.append(f"Could not remove symlink: {e}")

    if os.path.isfile(exe_fingerprinter_path):
        try:
            os.remove(exe_fingerprinter_path)
        except OSError as e:
            errors.append(f"Could not remove ExeFingerprinter.AppImage: {e}")

    cache_dir = os.path.join(install_dir, "image_cache")
    if os.path.exists(cache_dir):
        try:
            shutil.rmtree(cache_dir)
        except OSError as e:
            errors.append(f"Could not remove image cache directory: {e}")

    if restore:
        try:
            shutil.copy2(backup_path, symlink_path)
            os.chmod(symlink_path, 0o755)
        except OSError as e:
            errors.append(f"Could not restore ACCELA backup: {e}")

    if os.path.isfile(desktop_entry):
        try:
            with open(desktop_entry, "r") as f:
                content = f.read()
            content = content.replace("Name=EXE-fingerprinter", "Name=ACCELA")
            with open(desktop_entry, "w") as f:
                f.write(content)
            try:
                subprocess.run(
                    ["update-desktop-database", os.path.dirname(desktop_entry)],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        except OSError as e:
            errors.append(f"Could not update desktop entry: {e}")

    if errors:
        ThemedMessageBox.warning(dialog, "Uninstall — Partial", "\n".join(errors))
    else:
        msg = "EXE-fingerprinter has been uninstalled."
        if restore:
            msg += "\nOriginal ACCELA has been restored."
        ThemedMessageBox.information(dialog, "Done", msg)
