import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QToolBar

from core.runtime import ApplicationSetup
from core.startup import StartupPaths, setupConfiguration
from test_log_controls import APP, highlights, visible_text
from ui.log_view import COLORS
from ui.main_window import MainWindow


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for log file load")


class LogFileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = StartupPaths(self.root / "data", self.root / "config", self.root / "state.json")
        self.manager = setupConfiguration(self.paths)
        self.manager.config.general.auto_start = False
        self.manager.config.general.persist = False
        self.manager.config.general.max_display_lines = 100
        self.window = MainWindow(ApplicationSetup(self.manager, self.paths))
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        pump_until(lambda: self.window.file_loader is None and not self.window.isVisible())
        self.temp.cleanup()

    def load(self, path):
        self.window.loadLogFile(path)
        pump_until(lambda: self.window.file_loader is None)

    def test_split_panes_keep_independent_controls_and_contents(self):
        window = self.window
        self.assertEqual(window.splitter.orientation(), Qt.Orientation.Vertical)
        self.assertTrue(window.file_pane.isHidden())
        self.assertFalse(window.pane_selector.model().item(1).isEnabled())
        window.log.appendMessage("socket Alpha alpha")
        window.search.setText("Alpha")
        window.match_case.setChecked(True)
        window.line_numbers_action.setChecked(True)
        window._setFilter("protocol", "TCP")
        path = self.root / "existing.log"
        path.write_text("ERROR: file Alpha alpha 温度=36.192\n    details\nINFO other", encoding="utf-8")
        self.manager.config.socket.encoding = "ascii"
        self.load(path)
        self.assertTrue(window.file_pane.isVisible())
        self.assertTrue(window.pane_selector.model().item(1).isEnabled())
        self.assertIs(window.active_log, window.file_log)
        self.assertEqual(window.search.text(), "")
        self.assertFalse(window.match_case.isChecked())
        self.assertFalse(window.line_numbers_action.isChecked())
        self.assertEqual(window.file_log.toPlainText(), path.read_text(encoding="utf-8"))
        window.search.setText("alpha")
        window._setFilter("severity", "ERROR")
        self.assertEqual(highlights(window.file_log), ["Alpha", "alpha"])
        QTest.mouseClick(window.search_buttons["last"], Qt.MouseButton.LeftButton)
        self.assertEqual(window.file_log.textCursor().selectedText(), "alpha")
        self.assertNotIn("INFO other", visible_text(window.file_log))
        window.pane_selector.setCurrentIndex(0)
        self.assertEqual(window.search.text(), "Alpha")
        self.assertTrue(window.match_case.isChecked())
        self.assertTrue(window.line_numbers_action.isChecked())
        self.assertEqual(window.log.filters["protocol"], "TCP")
        self.assertEqual(window.file_log.filters["severity"], "ERROR")
        window.log.appendMessage("socket arrived during file view")
        self.assertNotIn("socket arrived", window.file_log.toPlainText())
        QTest.mouseClick(window.file_log.viewport(), Qt.MouseButton.LeftButton)
        self.assertIs(window.active_log, window.file_log)
        self.assertEqual(window.search.text(), "alpha")
        toolbar = window.findChild(QToolBar)
        clear = next(action for action in toolbar.actions() if action.text() == "Clear display")
        QTest.mouseClick(toolbar.widgetForAction(clear), Qt.MouseButton.LeftButton)
        self.assertEqual(window.file_log.toPlainText(), "")
        self.assertIn("socket arrived", window.log.toPlainText())
        replacement = self.root / "replacement.log"
        replacement.write_text("INFO alpha hidden\nERROR alpha restored", encoding="utf-8")
        self.load(replacement)
        self.assertEqual(visible_text(window.file_log), "ERROR alpha restored")
        self.assertEqual(window.search.text(), "alpha")
        self.assertEqual(window.log.filters["protocol"], "TCP")

    def test_file_pane_remains_hidden_until_a_successful_load(self):
        self.assertTrue(self.window.file_pane.isHidden())
        QTimer.singleShot(0, lambda: next(dialog for dialog in self.window.findChildren(QFileDialog) if dialog.isVisible()).reject())
        self.window.open_log_action.trigger()
        self.assertTrue(self.window.file_pane.isHidden())
        with self.assertLogs("LogServer", level="WARNING"):
            self.load(self.root / "missing.log")
        self.assertTrue(self.window.file_pane.isHidden())
        self.assertFalse(self.window.pane_selector.model().item(1).isEnabled())
        self.assertTrue(self.window.error.isVisible())
        empty = self.root / "empty.log"
        empty.write_text("", encoding="utf-8")
        self.load(empty)
        self.assertTrue(self.window.file_pane.isVisible())
        self.assertFalse(self.window.error.isVisible())
        self.assertEqual(self.window.file_log.toPlainText(), "")

    def test_close_log_file_clears_hides_and_can_reopen(self):
        window = self.window
        action = next(action for action in window.menuBar().actions()[0].menu().actions()
                      if action.text() == "Close log file")
        self.assertFalse(action.isEnabled())
        with self.assertLogs("LogServer", level="WARNING"):
            self.load(self.root / "missing.log")
        self.assertFalse(action.isEnabled())
        window.log.appendMessage("INFO socket retained")
        window.search.setText("socket")
        path = self.root / "close.log"
        path.write_text("ERROR file contents", encoding="utf-8")
        self.load(path)
        self.assertTrue(action.isEnabled())
        action.trigger()
        self.assertFalse(action.isEnabled())
        self.assertEqual(window.file_log.toPlainText(), "")
        self.assertTrue(window.file_pane.isHidden())
        self.assertFalse(window.pane_selector.model().item(1).isEnabled())
        self.assertIs(window.active_log, window.log)
        self.assertEqual(window.search.text(), "socket")
        self.assertEqual(window.log.toPlainText(), "INFO socket retained")
        self.assertEqual(window.file_label.text(), "Log file: no file loaded")
        self.assertEqual(window.file_status.text(), "")
        path.write_text("", encoding="utf-8")
        self.load(path)
        self.assertTrue(action.isEnabled())
        self.assertTrue(window.file_pane.isVisible())
        window.loadLogFile(path)
        action.trigger()
        window._fileLoaded((str(path), [("INFO queued result", {})], 0))
        pump_until(lambda: window.file_loader is None)
        self.assertFalse(action.isEnabled())
        self.assertTrue(window.file_pane.isHidden())
        self.assertEqual(window.file_log.toPlainText(), "")

    def test_file_metadata_coloring_and_continuation_filters(self):
        stamp = "2026-10-29T11:59:15.720+08:00"
        lines = [
            f"{stamp} 10.10.0.146 TCP [ERROR] - |pump|temperature=36.192",
            "    traceback detail",
            f"{stamp} [::1] [UDP] WARNING - |fan|temperature=120",
            '{"level":"info","device":"pico","message":"literal <b>text</b>"}',
            "2026-10-29 11:59:16 [ warn ] plain temperature",
        ]
        path = self.root / "formats.log"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.load(path)
        view = self.window.file_log
        self.assertEqual(view.toPlainText(), "\n".join(lines))
        self.assertEqual(view.filterValues()["device"], {"pump", "fan", "pico", ""})
        self.assertIn("::1", view.filterValues()["ip"])
        self.assertEqual(view.filterValues()["protocol"], {"TCP", "UDP", ""})
        self.window._setFilter("device", "pump")
        self.assertEqual(visible_text(view), "\n".join(lines[:2]))
        self.window._resetFilters()
        self.window._setFilter("protocol", "UDP")
        self.assertEqual(visible_text(view), lines[2])
        self.window._resetFilters()
        self.window.search.setText("temperature")
        self.assertEqual(highlights(view), ["temperature", "temperature", "temperature"])
        mode = "dark" if view.palette().base().color().lightness() < 128 else "light"
        for index, level in ((0, "ERROR"), (2, "WARNING"), (4, "WARNING")):
            block = view.document().findBlockByNumber(index)
            word = "warn" if index == 4 else level
            position = block.text().index(word)
            run = next(run for run in block.layout().formats() if run.start <= position < run.start + run.length)
            self.assertEqual(run.format.foreground().color().name(), COLORS[mode][level])

    def test_file_dialog_defaults_open_and_cancel(self):
        path = self.root / "dialog.log"
        path.write_text("INFO dialog content", encoding="utf-8")
        observed = {}

        def choose():
            dialog = self.window.findChild(QFileDialog)
            observed.update(suffix=dialog.defaultSuffix(), filters=dialog.nameFilters(), mode=dialog.fileMode())
            dialog.selectFile(str(path))
            dialog.accept()

        QTimer.singleShot(0, choose)
        self.window.open_log_action.trigger()
        pump_until(lambda: self.window.file_loader is None)
        self.assertEqual(observed["suffix"], "log")
        self.assertEqual(observed["filters"][0], "Log files (*.log)")
        self.assertEqual(observed["mode"], QFileDialog.FileMode.ExistingFile)
        self.assertEqual(self.window.file_log.toPlainText(), "INFO dialog content")
        APP.processEvents()
        QTimer.singleShot(0, lambda: next(dialog for dialog in self.window.findChildren(QFileDialog) if dialog.isVisible()).reject())
        self.window.open_log_action.trigger()
        self.assertEqual(self.window.file_log.toPlainText(), "INFO dialog content")
        self.assertIsNone(self.window.file_loader)

    def test_failed_load_preserves_previous_file_and_reports_without_traceback(self):
        path = self.root / "previous.log"
        path.write_text("WARNING keep this file", encoding="utf-8")
        self.load(path)
        label = self.window.file_label.text()
        with self.assertLogs("LogServer", level="WARNING") as captured:
            self.load(self.root / "missing.log")
            self.load(self.root)
        self.assertEqual(self.window.file_log.toPlainText(), "WARNING keep this file")
        self.assertEqual(self.window.file_label.text(), label)
        self.assertTrue(self.window.file_status.isVisible())
        self.assertIn("Cannot load", self.window.file_status.text())
        self.assertNotIn("Traceback", "\n".join(captured.output))
        self.assertTrue(self.window.open_log_action.isEnabled())

    def test_socket_receives_while_file_pane_is_selected(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.manager.config.socket.port = reserve.getsockname()[1]
        self.window.startServer()
        pump_until(lambda: self.window._server_state == "Listening")
        path = self.root / "offline.log"
        path.write_text("INFO offline snapshot", encoding="utf-8")
        self.load(path)
        self.window.search.setText("offline")
        with socket.create_connection(self.window.server.address[:2], timeout=2) as client:
            client.sendall(b"INFO live received\n")
        pump_until(lambda: self.window.received == 1)
        self.assertIn("live received", self.window.log.toPlainText())
        self.assertEqual(self.window.file_log.toPlainText(), "INFO offline snapshot")
        self.assertIs(self.window.active_log, self.window.file_log)
        self.assertEqual(self.window.search.text(), "offline")

    def test_bounded_history_bom_bad_bytes_and_close_during_read(self):
        path = self.root / "large.log"
        path.write_bytes(b"\xef\xbb\xbfERROR old header\n" + b"    detail\n" * 120 + b"tail \xff\n")
        self.load(path)
        self.assertEqual(self.window.file_log.blockCount(), 100)
        self.assertIn("older lines omitted", self.window.file_status.text())
        self.assertIn("\ufffd", self.window.file_log.toPlainText())
        self.window._setFilter("severity", "ERROR")
        self.assertIn("tail", visible_text(self.window.file_log))
        self.window.loadLogFile(path)
        self.window.close()
        pump_until(lambda: self.window.file_loader is None and not self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
