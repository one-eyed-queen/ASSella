import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser

from PyQt6.QtCore import Qt, QUrl, QMetaObject
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)
from ui.widgets.modern_dropdown import ModernComboBox
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialogs.settings_tabs.components_card import (
    create_components_card,
    require_component,
)

from utils.helpers import get_venv_python
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def create_tools_tab(dialog) -> QWidget:
    """Create the Tools settings tab."""
    from PyQt6.QtWidgets import QScrollArea

    tab = QWidget()
    tab_lay = QVBoxLayout(tab)
    tab_lay.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    tab_lay.addWidget(scroll)
    content = QWidget()
    content.setStyleSheet("background: transparent;")
    scroll.setWidget(content)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(12, 10, 16, 14)
    layout.setSpacing(14)

    # Tools Card
    tools_card, tools_layout = dialog._create_card_frame("Tools")

    tools_btn_row = QHBoxLayout()
    tools_btn_row.setContentsMargins(0, 4, 0, 4)
    tools_btn_row.setSpacing(10)

    tool_btn_style = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
            color: #FFFFFF;
            padding: 7px 14px;
            font-size: 9.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.32);
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.03) !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            color: rgba(255, 255, 255, 0.3) !important;
        }
    """

    dialog.configure_achievements_btn = QPushButton("Achievements")
    dialog.configure_achievements_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.configure_achievements_btn.setStyleSheet(tool_btn_style)
    dialog.configure_achievements_btn.setToolTip("Perform one-time setup and authenticate Steam for achievements.")
    dialog.configure_achievements_btn.clicked.connect(lambda: run_schema_grabber_manually(dialog))
    tools_btn_row.addWidget(dialog.configure_achievements_btn)

    dialog.steamless_py_btn = QPushButton("Steamless (Python)")
    dialog.steamless_py_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.steamless_py_btn.setStyleSheet(tool_btn_style)
    dialog.steamless_py_btn.setToolTip("Run Steamless AIO (Python) manually on a game .exe.")
    dialog.steamless_py_btn.clicked.connect(lambda: run_steamless_aio_manually(dialog))
    tools_btn_row.addWidget(dialog.steamless_py_btn)

    dialog.steamless_legacy_btn = QPushButton("Steamless (.NET CLI)")
    dialog.steamless_legacy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.steamless_legacy_btn.setStyleSheet(tool_btn_style)
    dialog.steamless_legacy_btn.setToolTip("Run Steamless (.NET 9 CLI) manually on a game .exe.")
    dialog.steamless_legacy_btn.clicked.connect(lambda: run_steamless_manually(dialog))
    tools_btn_row.addWidget(dialog.steamless_legacy_btn)

    tools_btn_row.addStretch()
    tools_layout.addLayout(tools_btn_row)
    layout.addWidget(tools_card)

    # Logging Configuration Card
    log_card, log_layout = dialog._create_card_frame("Logging Configuration")

    log_row = QHBoxLayout()
    log_row.setContentsMargins(0, 4, 0, 4)
    log_row.setSpacing(24)

    level_box = QHBoxLayout()
    level_box.setSpacing(8)
    level_label = QLabel("Log Level:")
    level_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
    level_label.setToolTip(
        "Minimum severity of messages to log.\n"
        "Select NONE to disable all logging (improves performance)."
    )
    dialog.log_level_combo = ModernComboBox()
    dialog.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "NONE"])
    _current_level = dialog.settings.value("log_filter_level", "DEBUG") or "DEBUG"
    idx = dialog.log_level_combo.findText(_current_level)
    dialog.log_level_combo.setCurrentIndex(idx if idx >= 0 else 0)
    level_box.addWidget(level_label)
    level_box.addWidget(dialog.log_level_combo)
    log_row.addLayout(level_box)

    cat_box = QHBoxLayout()
    cat_box.setSpacing(8)
    cat_label = QLabel("Log Filter:")
    cat_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
    cat_label.setToolTip("Restrict logs to a specific module group.")
    dialog.log_category_combo = ModernComboBox()
    dialog.log_category_combo.addItems([
        "All Modules",
        "Only Steam Client & API",
        "Only Downloads & Manifests",
        "Only Database & Library",
    ])
    _current_cat = dialog.settings.value("log_filter_category", "All Modules") or "All Modules"
    cat_idx = dialog.log_category_combo.findText(_current_cat)
    dialog.log_category_combo.setCurrentIndex(cat_idx if cat_idx >= 0 else 0)
    cat_box.addWidget(cat_label)
    cat_box.addWidget(dialog.log_category_combo)
    log_row.addLayout(cat_box)

    log_row.addStretch()
    log_layout.addLayout(log_row)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 4, 0, 0)
    btn_row.setSpacing(10)

    btn_style = f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 6px 14px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """

    open_log_btn = QPushButton("Open Log Folder")
    open_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    open_log_btn.setStyleSheet(btn_style)
    from utils.logger import open_log_directory
    open_log_btn.clicked.connect(lambda: open_log_directory())
    open_log_btn.setToolTip("Open the EXE-fingerprinter log directory in your file manager.")
    btn_row.addWidget(open_log_btn)

    export_logs_btn = QPushButton("Export Diagnostic Logs")
    export_logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    export_logs_btn.setStyleSheet(btn_style)
    export_logs_btn.clicked.connect(lambda: export_diagnostic_logs(dialog))
    export_logs_btn.setToolTip("Bundle recent EXE-fingerprinter logs and a short system summary into a ZIP archive.")
    btn_row.addWidget(export_logs_btn)

    btn_row.addStretch()
    log_layout.addLayout(btn_row)
    layout.addWidget(log_card)

    # Optional components (Goldberg / Steamless / SLScheevo) are no longer
    # bundled - this is the one place they can be downloaded or updated.
    layout.addWidget(create_components_card(dialog))

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Tools")
    return tab


def export_diagnostic_logs(dialog) -> None:
    """Bundle the recent app logs and a short system summary into a ZIP archive."""
    import platform
    import zipfile
    from datetime import datetime
    from utils.logger import get_base_path
    from utils.paths import Paths
    from utils.version import app_version

    # Flush current log handlers so in-memory logs are committed to disk
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception as e:
            logger.debug(f"[Diagnostics] Error flushing log handler: {e}")

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"exe_fingerprinter_diagnostics_{timestamp_str}.zip"
    default_dest = str(Path.home() / default_name)

    save_path, _ = ThemedFileDialog.getSaveFileName(
        dialog,
        "Export Diagnostic Logs",
        default_dest,
        "Zip Archives (*.zip)",
    )
    if not save_path:
        return

    collected = []
    try:
        with zipfile.ZipFile(save_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Latest 2 EXE-fingerprinter logs
            log_dir = get_base_path() / "logs"
            if log_dir.is_dir():
                log_files = sorted(
                    log_dir.glob("exe_fingerprinter_*.log"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for log_file in log_files[:2]:
                    zf.write(log_file, arcname=f"logs/{log_file.name}")
                    collected.append(f"EXE-fingerprinter Log: {log_file.name}")

            # 2. A short summary: versions, platform, backend path, settings that
            #    change behaviour. No tokens, passwords or API keys.
            dll = Paths.depot_downloader_dll()
            dotnet = shutil.which("dotnet") or "(not on PATH)"
            interesting = [
                "accent_color", "background_color", "log_filter_level",
                "hide_macos_depots", "hide_android_depots", "generate_achievements",
                "auto_run_steamless_post_install", "isp_bypass_mode",
                "enable_workshop_downloader",
            ]
            summary = [
                f"EXE-fingerprinter {app_version}",
                f"Platform: {platform.platform()} ({platform.machine()})",
                f"Python: {sys.version.split()[0]}",
                f"Executable: {sys.executable}",
                f"DepotDownloader backend: {dll} (exists={dll.exists()})",
                f"dotnet: {dotnet}",
                "",
                "Settings:",
            ]
            for key in interesting:
                summary.append(f"  {key} = {dialog.settings.value(key, '')!r}")
            zf.writestr("system.txt", "\n".join(summary) + "\n")
            collected.append("System summary")

        summary_text = "\n• " + "\n• ".join(collected) if collected else "No files collected."
        ThemedMessageBox.information(
            dialog,
            "Diagnostics Exported",
            f"Diagnostic log archive successfully saved to:\n{save_path}\n\nContents:{summary_text}",
        )
    except Exception as e:
        logger.error(f"Failed to export diagnostic archive: {e}", exc_info=True)
        ThemedMessageBox.critical(
            dialog,
            "Export Failed",
            f"Failed to create diagnostic archive:\n{e}",
        )


def add_tool_button(layout: QVBoxLayout, text: str, tooltip: str, slot) -> QPushButton:
    """Helper to add a left-aligned tool button with an always-visible description label stacked below."""
    row_box = QVBoxLayout()
    row_box.setContentsMargins(0, 2, 0, 6)
    row_box.setSpacing(4)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 0, 0, 0)

    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
            color: #FFFFFF;
            padding: 7px 16px;
            font-size: 9.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.32);
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.06) !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            color: rgba(255, 255, 255, 0.38) !important;
        }
    """)
    btn.clicked.connect(slot)
    btn_row.addWidget(btn)
    btn_row.addStretch()
    row_box.addLayout(btn_row)

    if tooltip:
        desc_lbl = QLabel(tooltip)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
        desc_lbl.setWordWrap(True)
        row_box.addWidget(desc_lbl)

    layout.addLayout(row_box)
    return btn


def run_schema_grabber_manually(dialog) -> None:
    from ui.dialogs.settings_tabs.components_card import require_component
    if not require_component(dialog, "slscheevo"):
        return

    helper_path = Paths.deps("SLScheevo/login_helper.py")
    if not helper_path.exists():
        helper_path = Paths.deps("schema-grabber/login_helper.py")
    if not helper_path.exists():
        ThemedMessageBox.critical(dialog, "Error", f"Achievements helper missing at: {helper_path}")
        return

    cmd = []
    py = get_venv_python()
    cmd.append(py if py else ("python" if sys.platform == "win32" else "python3"))
    cmd.append(str(helper_path))
    os.environ["EXE_FINGERPRINTER_SRC"] = str(Paths.BASE_DIR)
    launch_terminal_command(cmd, str(helper_path.parent))


def launch_terminal_command(cmd: list, cwd: str, needs_env: bool = False) -> bool:
    cmd_str = [str(part) for part in cmd]
    cwd = str(cwd)
    if sys.platform == "win32":
        q_cmd = " ".join([f'"{c}"' if " " in str(c) else str(c) for c in cmd_str])
        try:
            subprocess.Popen(f'start cmd /k "cd /d {cwd} && {q_cmd}"', shell=True)
            return True
        except OSError:
            pass
    else:
        terms = [
            ["wezterm", "start", "--always-new-process", "--"] + cmd_str,
            ["konsole", "-e"] + cmd_str,
            ["gnome-terminal", "--"] + cmd_str,
            ["ptyxis", "--"] + cmd_str,
            ["alacritty", "-e"] + cmd_str,
            ["tilix", "-e"] + cmd_str,
            ["xfce4-terminal", "-e"] + cmd_str,
            ["terminator", "-x"] + cmd_str,
            ["mate-terminal", "-e"] + cmd_str,
            ["lxterminal", "-e"] + cmd_str,
            ["xterm", "-e"] + cmd_str,
            ["kitty", "-e"] + cmd_str,
        ]
        for t in terms:
            try:
                t_cmd = [str(part) for part in t]
                subprocess.Popen(t_cmd, cwd=cwd)
                return True
            except FileNotFoundError:
                continue

    ThemedMessageBox.information(
        None,
        "Terminal Not Found",
        "Could not automatically launch a terminal.\n"
        "Please open a terminal and run:",
        informative=" ".join(cmd_str),
    )
    return False


def run_steamless_manually(dialog) -> None:
    if not require_component(dialog, "steamless"):
        return
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_manually(path)


def run_steamless_aio_manually(dialog) -> None:
    if not require_component(dialog, "steamless"):
        return
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_aio_manually(path)


def browse_aio_script(dialog) -> None:
    current = getattr(dialog, "steamless_aio_path_edit", None)
    text = current.text() if current else ""
    current_path = text or os.path.expanduser("~/Downloads")
    start_dir = os.path.dirname(current_path) if os.path.isfile(current_path) else current_path
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog,
        "Select Steamless AIO Script",
        start_dir,
        "Shell Scripts (*.sh);;All Files (*)",
    )
    if path and current:
        current.setText(path)
        get_settings().setValue("steamless_aio_path", path)


def update_achievements_button_state(dialog) -> None:
    if hasattr(dialog, "configure_achievements_btn") and dialog.configure_achievements_btn:
        is_enabled = dialog.achievements_checkbox.isChecked() if dialog.achievements_checkbox else False
        dialog.configure_achievements_btn.setEnabled(is_enabled)
