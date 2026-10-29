import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.messages import parseMessage
from core.runtime import ApplicationSetup
from core.startup import StartupPaths, setupConfiguration
from ui.appearance import applyAppearance
from ui.log_view import LogView
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


def visible_text(view):
    lines = []
    block = view.document().firstBlock()
    while block.isValid():
        if block.isVisible():
            lines.append(block.text())
        block = block.next()
    return "\n".join(lines)


def highlights(view):
    spans = []
    block = view.document().firstBlock()
    while block.isValid():
        if block.isVisible():
            for run in block.layout().formats():
                if run.format.background().style() != Qt.BrushStyle.NoBrush:
                    cursor = QTextCursor(view.document())
                    cursor.setPosition(block.position() + run.start)
                    cursor.setPosition(block.position() + run.start + run.length, QTextCursor.MoveMode.KeepAnchor)
                    spans.append(cursor.selectedText())
        block = block.next()
    return spans


class LogControlsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = StartupPaths(root / "data", root / "config", root / "state.json")
        self.manager = setupConfiguration(self.paths)
        self.manager.config.general.auto_start = False
        self.manager.config.general.persist = False
        self.manager.config.general.max_display_lines = 100
        self.views = []

    def tearDown(self):
        for view in self.views:
            view.close()
        APP.processEvents()
        self.temp.cleanup()

    def view(self):
        view = LogView()
        view.configure(self.manager.config.general)
        self.views.append(view)
        return view

    def window(self):
        window = MainWindow(ApplicationSetup(self.manager, self.paths))
        self.views.append(window)
        window.show()
        APP.processEvents()
        return window

    def append(self, view, body, severity="INFO", ip="10.10.0.146", protocol="tcp", device="sensor"):
        payload = json.dumps({"message": body, "level": severity, "device": device}).encode()
        message = parseMessage(payload, (ip, 5514), protocol, "utf-8")
        view.appendMessage(message.line, message)
        return message

    def test_device_parsing_handles_plain_invalid_and_json_values(self):
        for payload, expected in ((b"INFO plain", ""), (b'{"broken":', ""), (b"[]", ""),
                                  (b'{"message":"plain","dvc":"pico"}', "pico"),
                                  (b'{"message":"plain","device":"pump"}', "pump"),
                                  (b'{"message":"plain","device":null}', ""),
                                  (b'{"message":"plain","device":{"id":1}}', ""),
                                  (b'{"message":"plain","device":"pump\\n\\t\\u0000"}', "pump  ")):
            with self.subTest(payload=payload):
                message = parseMessage(payload, ("::1", 5514), "udp", "utf-8")
                self.assertEqual(message.device, expected)
                self.assertIsInstance(message.line, str)

    def test_filters_combine_and_keep_multiline_live_records(self):
        view = self.view()
        first = self.append(view, "first\nfirst detail", severity="ERROR", device="pump|1")
        second = self.append(view, "second\nsecond detail", protocol="udp", ip="::1", device="pump|1")
        third = self.append(view, "third", severity="ERROR", device="other")
        for filters, included, excluded in (
                ({"severity": "ERROR"}, ("first", "third"), ("second",)),
                ({"ip": "::1"}, ("second",), ("first", "third")),
                ({"protocol": "UDP"}, ("second",), ("first", "third")),
                ({"device": "pump|1"}, ("first", "second"), ("third",)),
                ({"severity": "ERROR", "ip": first.client, "protocol": "TCP", "device": "pump|1"}, ("first",), ("second", "third"))):
            with self.subTest(filters=filters):
                view.setFilters(**filters)
                shown = visible_text(view)
                for word in included:
                    self.assertIn(word, shown)
                for word in excluded:
                    self.assertNotIn(word, shown)
        self.append(view, "matching live\nmatching detail", severity="ERROR", device="pump|1")
        self.append(view, "hidden live\u2029hidden detail", severity="ERROR", device="other")
        self.assertIn("matching detail", visible_text(view))
        self.assertNotIn("hidden live", visible_text(view))
        self.assertNotIn("hidden detail", visible_text(view))
        view.setFilters(device="missing")
        self.assertEqual(visible_text(view), "")
        view.setFilters()
        self.assertIn(second.line, view.toPlainText())
        self.assertIn(third.line, visible_text(view))
        self.assertIn("hidden live", visible_text(view))

    def test_filtered_history_stays_bounded_and_clear_discards_it(self):
        view = self.view()
        view.resize(320, 200)
        view.show()
        view.setFilters(device="not present")
        for index in range(120):
            self.append(view, f"entry {index}\ndetail {index}", device=f"sensor-{index}")
        APP.processEvents()
        self.assertEqual(view.blockCount(), 100)
        self.assertNotIn("sensor-0", view.filterValues()["device"])
        self.assertIn("sensor-119", view.filterValues()["device"])
        self.assertEqual(visible_text(view), "")
        view.setFilters(device="sensor-119")
        self.assertIn("detail 119", visible_text(view))
        self.assertNotIn("entry 118", visible_text(view))
        view.clear()
        self.assertEqual(view.filterValues()["device"], set())
        view.setFilters()
        self.append(view, "fresh")
        self.assertNotIn("entry", visible_text(view))

    def test_search_highlights_literal_unicode_text_case_and_live_appends(self):
        view = self.view()
        view.appendMessage("🌡️ Alpha alpha ALPHA [a.b]\nAlpha")
        cursor = view.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        view.setTextCursor(cursor)
        selection = view.textCursor().selectedText()
        view.setSearch("alpha")
        self.assertEqual(highlights(view), ["Alpha", "alpha", "ALPHA", "Alpha"])
        view.setSearch("alpha", True)
        self.assertEqual(highlights(view), ["alpha"])
        view.setSearch("[a.b]")
        self.assertEqual(highlights(view), ["[a.b]"])
        view.appendMessage("[a.b] arrived")
        APP.processEvents()
        self.assertEqual(highlights(view), ["[a.b]", "[a.b]"])
        self.assertEqual(view.textCursor().selectedText(), selection)
        view.setSearch("")
        self.assertEqual(highlights(view), [])

    def test_toolbar_statusbar_and_line_number_toggle(self):
        window = self.window()
        self.append(window.log, "Alpha alpha\nwrapped " + "long message " * 40, device="pico")
        self.append(window.log, "other", severity="ERROR", protocol="udp", device="pump")
        window.filter_menu.aboutToShow.emit()
        menus = {action.text(): action.menu() for action in window.filter_menu.actions() if action.menu()}
        self.assertEqual(set(menus), {"Severity", "IP", "Protocol", "Device"})
        next(action for action in menus["Device"].actions() if action.text() == "pico").trigger()
        self.assertNotIn("other", visible_text(window.log))
        self.assertEqual(window.filter_button.text(), "Filter (1)")
        next(action for action in window.filter_menu.actions() if action.text() == "Reset filters").trigger()
        self.assertIn("other", visible_text(window.log))
        released = []
        menus["Device"].destroyed.connect(lambda: released.append(True))
        window.filter_menu.aboutToShow.emit()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(released, [True])
        QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(window.search.hasFocus())
        QTest.keyClicks(window.search, "alpha")
        self.assertEqual(highlights(window.log), ["Alpha", "alpha"])
        QTest.mouseClick(window.match_case, Qt.MouseButton.LeftButton)
        self.assertEqual(highlights(window.log), ["alpha"])
        self.assertTrue(window.log.line_numbers.isHidden())
        window.line_numbers_action.trigger()
        APP.processEvents()
        self.assertFalse(window.log.line_numbers.isHidden())
        self.assertGreater(window.log.viewport().geometry().left(), window.log.line_numbers.geometry().left())
        self.assertGreater(window.log.lineNumberAreaWidth(), 0)
        window.log.verticalScrollBar().setValue(window.log.verticalScrollBar().maximum())
        window.log.grab()
        window.line_numbers_action.trigger()
        self.assertEqual(window.log.lineNumberAreaWidth(), 0)

    def test_search_and_gutter_follow_appearance_and_font_changes(self):
        view = self.view()
        view.show()
        self.append(view, "🌡️ searchable")
        self.append(view, "hidden searchable", device="other")
        view.setFilters(device="sensor")
        view.setSearch("searchable")
        view.setLineNumbersVisible(True)
        settings = self.manager.config.general
        try:
            for mode in ("dark", "light"):
                settings.appearance = mode
                applyAppearance(settings)
                APP.processEvents()
                self.assertEqual(highlights(view), ["searchable"])
                highlighted = next(run for run in view.document().firstBlock().layout().formats()
                                   if run.format.background().style() != Qt.BrushStyle.NoBrush)
                self.assertEqual(highlighted.format.background().color(), view.palette().highlight().color())
                view.grab()
            before = view.lineNumberAreaWidth()
            font = view.font()
            font.setPointSizeF(font.pointSizeF() * 1.5)
            view.setFont(font)
            self.assertGreater(view.lineNumberAreaWidth(), before)
        finally:
            settings.appearance = "system"
            applyAppearance(settings)


if __name__ == "__main__":
    unittest.main()
