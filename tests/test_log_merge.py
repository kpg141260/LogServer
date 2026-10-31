import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from core.runtime import ApplicationSetup
from core.startup import StartupPaths, setupConfiguration
from test_log_controls import APP
from test_log_file import pump_until
from ui.main_window import MainWindow


class LogMergeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "first.log"
        self.second = self.root / "second.log"
        self.output = self.root / "merged.log"

    def merge(self, interrupted=lambda: False):
        from core.log_merge import mergeLogFiles
        return mergeLogFiles((self.first, self.second), self.output, interrupted)

    def test_merge_orders_unsorted_records_by_instant_and_keeps_details_and_duplicates(self):
        late = b"2026-10-31T08:00:03+08:00 INFO late\r\n    traceback \xff\r\n"
        early = b"2026-10-31T00:00:01Z INFO early\n"
        tie1 = b"2026-10-31T00:00:02Z INFO tie\n"
        tie2 = b"2026-10-31T08:00:02+08:00 INFO tie\n    detail\n"
        self.first.write_bytes(b"\xef\xbb\xbf" + late + early + tie1)
        self.second.write_bytes(tie2 + tie1)
        originals = self.first.read_bytes(), self.second.read_bytes()
        self.assertEqual(self.merge(), 5)
        self.assertEqual(self.output.read_bytes(), early + tie1 + tie2 + tie1 + late)
        self.assertEqual((self.first.read_bytes(), self.second.read_bytes()), originals)

    def test_local_comma_fraction_timestamps_empty_files_and_missing_final_newlines(self):
        from datetime import datetime, timezone
        local = datetime(2026, 10, 31, 8, 0, 0).astimezone(timezone.utc).isoformat()
        for first, second, expected, count in (
                (b"", b"", b"", 0),
                (b"", b"2026-10-31 08:00:00,123 INFO only", b"2026-10-31 08:00:00,123 INFO only", 1),
                (b"2026-10-31 08:00:00 INFO local", f"{local} INFO same instant\n".encode(),
                 b"2026-10-31 08:00:00 INFO local\n" + f"{local} INFO same instant\n".encode(), 2)):
            with self.subTest(first=first, second=second):
                self.first.write_bytes(first)
                self.second.write_bytes(second)
                self.assertEqual(self.merge(), count)
                self.assertEqual(self.output.read_bytes(), expected)

    def test_invalid_sources_and_timestamps_preserve_existing_output(self):
        self.first.write_text("2026-10-31T00:00:00Z INFO valid\n", encoding="utf-8")
        for data in (b"INFO no timestamp\n", b"2026-99-31T00:00:00Z INFO invalid\n",
                     b"2026-10-31T00:00:00Z INFO valid\n2026-10-31T25:00:00Z INFO invalid\n"):
            with self.subTest(data=data):
                self.second.write_bytes(data)
                self.output.write_bytes(b"keep previous output")
                with self.assertRaisesRegex(ValueError, r"second.log.*line [12]"):
                    self.merge()
                self.assertEqual(self.output.read_bytes(), b"keep previous output")
        self.second = self.root / "missing.log"
        with self.assertRaises(OSError):
            self.merge()
        self.assertEqual(self.output.read_bytes(), b"keep previous output")

    def test_output_cannot_replace_either_source_or_an_alias(self):
        self.first.write_bytes(b"2026-10-31T00:00:00Z INFO first\n")
        self.second.write_bytes(b"2026-10-31T00:00:01Z INFO second\n")
        originals = self.first.read_bytes(), self.second.read_bytes()
        alias = self.root / "alias.log"
        os.link(self.first, alias)
        for output in (self.first, self.second, alias):
            with self.subTest(output=output):
                self.output = output
                with self.assertRaisesRegex(ValueError, "source"):
                    self.merge()
                self.assertEqual((self.first.read_bytes(), self.second.read_bytes()), originals)
        self.second = self.first
        self.output = self.root / "merged.log"
        with self.assertRaisesRegex(ValueError, "different"):
            self.merge()
        self.assertFalse(self.output.exists())

    def test_seeded_record_ordering_and_invalid_timestamp_corpus(self):
        import random
        from datetime import datetime, timedelta, timezone
        randomizer = random.Random(31)
        expected = []
        base = datetime(2026, 10, 31, tzinfo=timezone.utc)
        for path in (self.first, self.second):
            records = []
            for index in range(80):
                instant = base + timedelta(microseconds=randomizer.randrange(20))
                stamp = instant.astimezone(timezone(timedelta(hours=randomizer.choice((-5, 0, 8)))))
                record = f"{stamp.isoformat()} INFO {path.name} {index}\n    details {index}\n".encode()
                records.append((instant, record))
            randomizer.shuffle(records)
            expected.extend(records)
            path.write_bytes(b"".join(record for _, record in records))
        self.merge()
        self.assertEqual(self.output.read_bytes(), b"".join(record for _, record in sorted(expected, key=lambda entry: entry[0])))
        for raw in (b"", b"\x00", b"\xff", b"2026-10-31T00:00:00.1234567Z INFO precision\n",
                    b"2026-10-31T00:00:00+99:99 INFO offset\n", b"2026-10-31T00:00:00+05:99 INFO offset\n",
                    b"2026-10-31T00:00:00Zevil\n"):
            with self.subTest(raw=raw):
                self.second.write_bytes(raw)
                before = self.output.read_bytes()
                if not raw:
                    self.merge()
                else:
                    with self.assertRaises(ValueError):
                        self.merge()
                    self.assertEqual(self.output.read_bytes(), before)

    def test_cancellation_and_output_failure_preserve_existing_files(self):
        self.first.write_bytes(b"2026-10-31T00:00:00Z INFO first\n" * 100)
        self.second.write_bytes(b"2026-10-31T00:00:01Z INFO second\n")
        self.output.write_bytes(b"keep previous output")
        checks = 0

        def interrupted():
            nonlocal checks
            checks += 1
            return checks > 10

        with self.assertRaises(InterruptedError):
            self.merge(interrupted)
        self.assertEqual(self.output.read_bytes(), b"keep previous output")
        self.output = self.root / "missing-directory" / "merged.log"
        with self.assertRaises(OSError):
            self.merge()
        self.assertFalse(self.output.exists())


class MergeWindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        paths = StartupPaths(self.root / "data", self.root / "config", self.root / "state.json")
        self.manager = setupConfiguration(paths)
        self.manager.config.general.auto_start = False
        self.manager.config.general.persist = False
        self.manager.config.general.max_display_lines = 2
        self.window = MainWindow(ApplicationSetup(self.manager, paths))
        self.window.show()
        APP.processEvents()
        self.first = self.root / "first.log"
        self.second = self.root / "second.log"
        self.first.write_text("2026-10-31T00:00:03Z INFO third\n2026-10-31T00:00:01Z INFO first\n    details\n", encoding="utf-8")
        self.second.write_text("2026-10-31T00:00:02Z INFO second\n", encoding="utf-8")

    def tearDown(self):
        self.window.close()
        pump_until(lambda: self.window.file_loader is None and
                   getattr(self.window, "merge_worker", None) is None and not self.window.isVisible())
        self.temp.cleanup()

    def load_pair(self):
        self.window.loadMergeFiles((self.first, self.second))
        pump_until(lambda: self.window.file_loader is None)

    def test_pair_previews_split_left_right_and_keep_independent_search(self):
        self.load_pair()
        window = self.window
        self.assertEqual(window.file_splitter.orientation(), Qt.Orientation.Horizontal)
        self.assertTrue(window.file_right_pane.isVisible())
        self.assertTrue(window.merge_button.isVisible())
        self.assertTrue(window.merge_button.isEnabled())
        self.assertLess(window.file_log.mapTo(window.file_pane, window.file_log.rect().topLeft()).x(),
                        window.second_file_log.mapTo(window.file_pane, window.second_file_log.rect().topLeft()).x())
        self.assertIn("INFO first", window.file_log.toPlainText())
        self.assertIn("INFO second", window.second_file_log.toPlainText())
        self.assertIn(str(self.first), window.file_label.text())
        self.assertIn(str(self.second), window.second_file_label.text())
        window.search.setText("first")
        QTest.mouseClick(window.second_file_log.viewport(), Qt.MouseButton.LeftButton)
        self.assertIs(window.active_log, window.second_file_log)
        window.search.setText("second")
        QTest.mouseClick(window.file_log.viewport(), Qt.MouseButton.LeftButton)
        self.assertIs(window.active_log, window.file_log)
        self.assertEqual(window.search.text(), "first")
        self.assertEqual(window.second_file_log.search_text, "second")

    def test_merge_menu_selection_and_button_save_all_lines(self):
        window = self.window
        observed = []

        def choose_input(index):
            dialog = next((dialog for dialog in window.findChildren(QFileDialog) if dialog.isVisible()), None)
            if dialog is None:
                return
            observed.append((dialog.fileMode(), dialog.windowTitle()))
            dialog.selectFile(str((self.first, self.second)[index]))
            if index == 0:
                QTimer.singleShot(0, lambda: choose_input(1))
            dialog.accept()

        QTimer.singleShot(0, lambda: choose_input(0))
        window.merge_log_action.trigger()
        pump_until(lambda: window.file_loader is None)
        self.assertEqual(observed, [(QFileDialog.FileMode.ExistingFile, "Select first log file to merge"),
                                    (QFileDialog.FileMode.ExistingFile, "Select second log file to merge")])
        self.assertEqual(window.merge_paths, (str(self.first), str(self.second)))
        window.pane_selector.setCurrentIndex(2)
        output = self.root / "merged.log"

        def choose_output():
            dialog = next(dialog for dialog in window.findChildren(QFileDialog) if dialog.isVisible())
            self.assertEqual(dialog.acceptMode(), QFileDialog.AcceptMode.AcceptSave)
            dialog.selectFile(str(output))
            dialog.accept()

        QTimer.singleShot(0, choose_output)
        QTest.mouseClick(window.merge_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: window.merge_worker is None and window.file_loader is None)
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "2026-10-31T00:00:01Z INFO first\n    details\n"
                         "2026-10-31T00:00:02Z INFO second\n2026-10-31T00:00:03Z INFO third\n")
        self.assertIn(str(output), window.file_label.text())
        self.assertEqual(window.file_log.toPlainText(),
                         "2026-10-31T00:00:02Z INFO second\n2026-10-31T00:00:03Z INFO third")
        self.assertTrue(window.file_pane.isVisible())
        self.assertTrue(window.file_right_pane.isHidden())
        self.assertTrue(window.merge_button.isHidden())
        self.assertEqual(window.second_file_log.toPlainText(), "")
        self.assertEqual(window.pane_selector.count(), 2)
        self.assertIs(window.active_log, window.file_log)
        self.assertIsNone(window.merge_paths)

    def test_cancelling_second_file_selection_preserves_existing_previews(self):
        self.load_pair()
        window = self.window
        contents = window.file_log.toPlainText(), window.second_file_log.toPlainText()
        paths = window.merge_paths
        titles = []

        def cancel_second():
            dialog = next((dialog for dialog in window.findChildren(QFileDialog) if dialog.isVisible()), None)
            if dialog is not None:
                titles.append(dialog.windowTitle())
                dialog.reject()

        def choose_first():
            dialog = next(dialog for dialog in window.findChildren(QFileDialog) if dialog.isVisible())
            titles.append(dialog.windowTitle())
            dialog.selectFile(str(self.second))
            QTimer.singleShot(0, cancel_second)
            dialog.accept()

        QTimer.singleShot(0, choose_first)
        window.merge_log_action.trigger()
        APP.processEvents()
        self.assertEqual(titles, ["Select first log file to merge", "Select second log file to merge"])
        self.assertIsNone(window.file_loader)
        self.assertEqual(window.merge_paths, paths)
        self.assertEqual((window.file_log.toPlainText(), window.second_file_log.toPlainText()), contents)

    def test_failed_pair_and_cancel_keep_previous_panes_and_report_errors(self):
        self.load_pair()
        window = self.window
        contents = window.file_log.toPlainText(), window.second_file_log.toPlainText()
        with self.assertLogs("LogServer", level="WARNING") as captured:
            window.loadMergeFiles((self.first, self.root / "missing.log"))
            pump_until(lambda: window.file_loader is None)
        self.assertNotIn("Traceback", "\n".join(captured.output))
        self.assertEqual((window.file_log.toPlainText(), window.second_file_log.toPlainText()), contents)
        self.assertTrue(window.merge_button.isEnabled())
        for action in (window.merge_log_action.trigger, window.merge_button.click):
            QTimer.singleShot(0, lambda: next(dialog for dialog in window.findChildren(QFileDialog) if dialog.isVisible()).reject())
            action()
            self.assertEqual((window.file_log.toPlainText(), window.second_file_log.toPlainText()), contents)
        with self.assertLogs("LogServer", level="WARNING"):
            window.loadMergeFiles((self.first,))
        self.assertIn("two", window.merge_status.text())

    def test_merge_failure_close_and_single_file_restore(self):
        self.load_pair()
        window = self.window
        self.second.write_text("INFO missing timestamp\n", encoding="utf-8")
        output = self.root / "merged.log"
        output.write_text("keep previous output", encoding="utf-8")
        with self.assertLogs("LogServer", level="WARNING") as captured:
            window.mergeLogFiles(output)
            pump_until(lambda: window.merge_worker is None)
        self.assertNotIn("Traceback", "\n".join(captured.output))
        self.assertIn("line 1", window.merge_status.text())
        self.assertEqual(output.read_text(encoding="utf-8"), "keep previous output")
        window.pane_selector.setCurrentIndex(2)
        window.loadLogFile(self.first)
        pump_until(lambda: window.file_loader is None)
        self.assertTrue(window.file_right_pane.isHidden())
        self.assertTrue(window.merge_button.isHidden())
        self.assertEqual(window.pane_selector.count(), 2)
        self.load_pair()
        window.loadMergeFiles((self.first, self.second))
        window.closeLogFile()
        pump_until(lambda: window.file_loader is None)
        self.assertTrue(window.file_pane.isHidden())
        self.assertTrue(window.file_right_pane.isHidden())
        self.assertTrue(window.merge_button.isHidden())
        self.assertIs(window.active_log, window.log)

    def test_close_cancels_merge_and_window_waits_for_worker(self):
        self.load_pair()
        window = self.window
        self.first.write_bytes(b"2026-10-31T00:00:00Z INFO entry\n" * 10000)
        output = self.root / "cancelled.log"
        output.write_bytes(b"previous output")
        window.mergeLogFiles(output)
        self.assertFalse(window.open_log_action.isEnabled())
        self.assertFalse(window.merge_log_action.isEnabled())
        window.closeLogFile()
        pump_until(lambda: window.merge_worker is None)
        self.assertEqual(output.read_bytes(), b"previous output")
        self.assertTrue(window.file_pane.isHidden())
        self.assertTrue(window.open_log_action.isEnabled())
        self.assertTrue(window.merge_log_action.isEnabled())
        self.load_pair()
        window.mergeLogFiles(output)
        window.close()
        pump_until(lambda: window.merge_worker is None and not window.isVisible())
        self.assertEqual(output.read_bytes(), b"previous output")


if __name__ == "__main__":
    unittest.main()
