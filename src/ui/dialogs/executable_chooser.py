"""
executable_chooser.py — The EXE-fingerprinter picker.

Lists every Windows executable (.exe) found inside the depots that would be
downloaded for a game, grouped per depot, with every entry pre-checked.
The user can uncheck anything they do not want; the result is handed to
DepotDownloader's `-filelist` per depot.
"""

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
)

from ui.dialogs.themed_dialog import install_themed_frame


class ExecutableChooserDialog(QDialog):
    """Checkbox list of .exe files from the game's depots, all pre-checked."""

    def __init__(self, entries, app_id="", parent=None, title="EXE-fingerprinter"):
        """entries: [{depot_id, path, size}, ...] — typically from exe_scan.list_executables_for_depots."""
        super().__init__(parent)
        self.app_id = str(app_id)
        self.entries = list(entries)
        self._by_path = {e["path"]: e for e in self.entries}
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(720, 520)
        self.setMinimumSize(520, 360)

        layout = install_themed_frame(self, margins=(14, 10, 14, 10), spacing=10)

        header = QLabel(
            f"Select which executables to download for AppID {self.app_id}.\n"
            f"All {len(self.entries)} executable(s) are pre-selected — uncheck what you do not want."
        )
        header.setStyleSheet("color: #FFFFFF; font-size: 9pt; background: transparent; border: none;")
        layout.addWidget(header)

        # Filters row: depot filter + select all / none + sort
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.depot_filter = QComboBox()
        self.depot_filter.addItem("All depots", "")
        seen_depots = []
        for e in self.entries:
            if e["depot_id"] not in seen_depots:
                seen_depots.append(e["depot_id"])
        for d in sorted(seen_depots, key=lambda x: (len(x), x)):
            self.depot_filter.addItem(f"Depot {d}", d)
        self.depot_filter.setFixedHeight(28)
        self.depot_filter.currentIndexChanged.connect(self._rebuild_list)
        controls.addWidget(self.depot_filter, 1)

        select_all_btn = QPushButton("Select all")
        select_none_btn = QPushButton("Select none")
        for b in (select_all_btn, select_none_btn):
            b.setFixedHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            controls.addWidget(b)
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        layout.addLayout(controls)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setStyleSheet(
            "QListWidget { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 6px; padding: 4px; color: #FFFFFF; font-size: 8.5pt; }"
            "QListWidget::item { padding: 2px 4px; }"
        )
        layout.addWidget(self.list_widget, 1)

        footer = QHBoxLayout()
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: rgba(255,255,255,0.65); font-size: 8.5pt; border: none;")
        footer.addWidget(self.summary_label, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.download_btn = QPushButton("Download executables")
        for b in (self.cancel_btn, self.download_btn):
            b.setFixedHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            footer.addWidget(b)
        self.cancel_btn.setStyleSheet("""
            QPushButton { background-color: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.16);
                          border-radius: 6px; color: #FFFFFF; padding: 4px 14px; font-size: 8.5pt; }
            QPushButton:hover { background-color: rgba(255,255,255,0.10); }
        """)
        self.download_btn.setStyleSheet("""
            QPushButton { background-color: #a1c9fd; color: #000000; font-weight: 600;
                          border: none; border-radius: 6px; padding: 4px 14px; font-size: 8.5pt; }
            QPushButton:hover { border: 1px solid #FFFFFF; }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        self.download_btn.clicked.connect(self.accept)
        layout.addLayout(footer)

        self.list_widget.itemChanged.connect(self._update_summary)
        # Listing thousands of executables takes a moment; keep the pointer
        # busy rather than leaving the window apparently frozen.
        from PyQt6.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._rebuild_list()
        finally:
            QApplication.restoreOverrideCursor()
        self._update_summary()

    # ------------------------------------------------------------------
    def _visible_entries(self):
        depot_filter = self.depot_filter.currentData() or ""
        if depot_filter:
            return [e for e in self.entries if e["depot_id"] == depot_filter]
        return list(self.entries)

    def _rebuild_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for e in self._visible_entries():
            item = QListWidgetItem()
            size_mb = e["size"] / (1024 * 1024) if e["size"] else 0.0
            item.setText(f"{e['path']}  —  {size_mb:.2f} MB  (depot {e['depot_id']})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self._update_summary()

    def _set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def _selected_entries(self):
        selected = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _update_summary(self):
        selected = self._selected_entries()
        total = sum(e["size"] for e in selected)
        size_mb = total / (1024 * 1024) if total else 0.0
        self.summary_label.setText(f"{len(selected)} of {len(self.entries)} executable(s) selected — {size_mb:.2f} MB")
        self.download_btn.setText(f"Download {len(selected)} executable(s)")

    def selected_files_by_depot(self):
        """Return {depot_id: [paths]} for everything checked."""
        selected = self._selected_entries()
        result = {}
        for e in selected:
            result.setdefault(e["depot_id"], []).append(e["path"])
        for depot_id in result:
            result[depot_id] = sorted(result[depot_id], key=str.lower)
        return result
