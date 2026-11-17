"""EXE-fingerprinter: dump every checked depot's manifest, collect the
.exe files across them, let the user uncheck any they do not want, and
download only those.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.themed_progress_dialog import ThemedProgressDialog

from ui.dialogs.depot_selection.dump_runner import ManifestDumpRunner
from ui.dialogs.depot_selection.manifest_dump import (
    ManifestDumpError,
    discard_keys_file,
    prepare_manifest_dump,
)


class FileCustomisationMixin:
    """Browse Files: manifest dump and the file-selection tree.

    Plus the EXE-fingerprinter: scan checked depots for executables and
    download only those.
    """

    def _checked_downloadable_depot_ids(self):
        """All checked depots that can actually be downloaded."""
        ids = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None or id_item.checkState() != Qt.CheckState.Checked:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander", "unreleased"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id not in ids:
                ids.append(depot_id)
        return ids

    def _on_exe_fingerprinter_clicked(self):
        depot_ids = self._checked_downloadable_depot_ids()
        if not depot_ids:
            ThemedMessageBox.warning(
                self, "Warning", "Please select at least one depot first."
            )
            return

        from ui.dialogs.exe_scan_runner import ExeScanThread

        progress = ThemedProgressDialog(
            "Scanning depots for executables…", "Cancel", 0, len(depot_ids), self
        )
        progress.setWindowTitle("EXE-fingerprinter")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        self._exe_scan_progress = progress

        thread = ExeScanThread(self.app_id, depot_ids, self)
        progress.canceled.connect(thread.cancel)

        def _on_progress(message, index):
            progress.setValue(max(progress.progress_bar.value(), index))
            progress.setLabelText(message)
            QApplication.processEvents()

        def _on_finished(entries, errors):
            progress.close()
            self._exe_scan_progress = None

            if not entries:
                detail = "\n".join(errors) if errors else "The selected depots contain no .exe files."
                ThemedMessageBox.information(
                    self,
                    "EXE-fingerprinter",
                    f"No executables were found.\n\n{detail}",
                )
                return

            # Exactly one executable across the selected depots: nothing to choose,
            # so skip the picker and ship that .exe automatically.
            if len(entries) == 1:
                only = entries[0]
                self.selected_files = {only["depot_id"]: [only["path"]]}
                self._exe_fingerprinter_mode = True
                self._cleanup_exe_scan_keys()
                ThemedMessageBox.information(
                    self,
                    "EXE-fingerprinter",
                    f"Found exactly one executable:\n\n{only['path']}\n\n"
                    f"Press OK at the bottom to download it.",
                )
                return

            from ui.dialogs.executable_chooser import ExecutableChooserDialog

            chooser = ExecutableChooserDialog(entries, app_id=self.app_id, parent=self)
            if chooser.exec():
                self.selected_files = chooser.selected_files_by_depot()
                self._exe_fingerprinter_mode = True
                count = sum(len(paths) for paths in self.selected_files.values())
                depot_count = len(self.selected_files)
                ThemedMessageBox.information(
                    self,
                    "EXE-fingerprinter",
                    f"{count} executable(s) selected across {depot_count} depot(s).\n"
                    f"Press OK at the bottom to download them.",
                )
            # Discard the keys files written by the dumps; they are consumed
            # by DepotDownloader itself, not by us to keep.
            self._cleanup_exe_scan_keys()

        thread.progress.connect(_on_progress)
        thread.finished_scan.connect(_on_finished)
        self._exe_scan_thread = thread
        thread.start()

    def _cleanup_exe_scan_keys(self):
        """Remove the depot keys files dumped during an exe scan."""
        import tempfile
        from pathlib import Path

        temp_dir = Path(tempfile.gettempdir()) / f"selective_manifests_{self.app_id}"
        try:
            keys = temp_dir / "depot.keys"
            if keys.exists():
                keys.unlink()
        except Exception:
            pass
        try:
            for per_keys in (temp_dir.glob("keys_*.vdf")):
                per_keys.unlink()
        except Exception:
            pass


    def _first_checked_depot_id(self):
        """The first checked depot that can actually be downloaded."""
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None or id_item.checkState() != Qt.CheckState.Checked:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander", "unreleased"):
                continue
            return str(id_item.data(Qt.ItemDataRole.UserRole))
        return None

    def _on_select_files_clicked(self):
        target_depot = self._first_checked_depot_id()
        if not target_depot:
            ThemedMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        try:
            dump = prepare_manifest_dump(self.app_id, target_depot)
        except ManifestDumpError as e:
            ThemedMessageBox.critical(self, "Error", str(e))
            return

        runner = ManifestDumpRunner(self, dump)
        runner.finished.connect(lambda ok, message: self._on_dump_finished(dump, ok, message))
        runner.start()

    def _on_dump_finished(self, dump, ok, message):
        """Open the file-selection tree over a listing that was written."""
        if not ok:
            ThemedMessageBox.critical(self, "Error", message)
            return

        from ui.dialogs.fileselection import FileSelectionDialog
        sel_dialog = FileSelectionDialog(
            app_id=dump.app_id,
            depot_id=dump.depot_id,
            manifest_txt_path=dump.txt_path,
            parent=self,
            manifest_file=dump.manifest_file,
            keys_path=dump.keys_path,
            manifest_id=dump.manifest_id,
        )
        try:
            if sel_dialog.exec():
                self.selected_files = sel_dialog.selected_files
                ThemedMessageBox.information(
                    self,
                    "Selection Confirmed",
                    f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                )
        finally:
            discard_keys_file(dump.keys_path)
