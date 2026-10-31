import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.messages import parseMessage
from core.runtime import ApplicationSetup
from core.startup import StartupPaths, setupConfiguration
from ui.appearance import applyAppearance, logColors
from ui.log_view import COLORS, LogView
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

    def test_paths_and_filenames_use_path_color_in_both_views(self):
        paths = ["/var/log/app120.log", "~/Library/Logs/app.log", "./logs/run36.log",
                 "../config/settings.json", "logs/archive/run.log", r"C:\Logs\app120.log",
                 r"\\server\share\app.log", "socket_handler.py", ".env",
                 '"/Users/Test User/log files/app.log"', r'"C:\Program Files\App\run.log"']
        text = "😀 " + ", ".join(paths) + " 10.10.0.146 36.192 120"
        for theme in ("dark", "light"):
            for source in ("socket", "file"):
                with self.subTest(theme=theme, source=source):
                    view = self.view()
                    self.manager.config.general.appearance = theme
                    applyAppearance(self.manager.config.general)
                    if source == "socket":
                        self.append(view, text)
                    else:
                        view.setLines([(text, dict.fromkeys(("severity", "ip", "protocol", "device"), ""))])
                    view.highlighter.rehighlight()
                    APP.processEvents()
                    block = view.document().firstBlock()
                    rendered = block.text()
                    def color_at(position):
                        offset = len(rendered[:position].encode("utf-16-le")) // 2
                        return next(run.format.foreground().color().name()
                                    for run in block.layout().formats()
                                    if run.start <= offset < run.start + run.length)
                    colors = logColors(theme == "dark")
                    for path in paths:
                        path = path.strip('"')
                        start = rendered.index(path)
                        for position in range(start, start + len(path)):
                            self.assertEqual(color_at(position), colors["path"], path)
                    for token, kind in (("10.10.0.146", "ip"), ("36.192", "number"), ("120", "number")):
                        self.assertEqual(color_at(rendered.rindex(token)), colors[kind])

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

    def test_search_navigation_uses_visible_matches_case_and_unicode_offsets(self):
        view = self.view()
        self.append(view, "🌡️ Alpha alpha", device="pico")
        self.append(view, "hidden alpha", device="other")
        self.append(view, "tail ALPHA", device="pico")
        view.setFilters(device="pico")
        view.setSearch("alpha")
        for direction, expected in (("first", "Alpha"), ("next", "alpha"), ("next", "ALPHA"),
                                    ("next", "Alpha"), ("previous", "ALPHA"), ("last", "ALPHA")):
            with self.subTest(direction=direction, expected=expected):
                self.assertTrue(view.navigateSearch(direction))
                self.assertEqual(view.textCursor().selectedText(), expected)
                self.assertNotIn("hidden", view.textCursor().block().text())
        first = len(view.toPlainText().split("Alpha")[0].encode("utf-16-le")) // 2
        view.navigateSearch("first")
        self.assertEqual(view.textCursor().selectionStart(), first)
        self.append(view, "new alpha", device="pico")
        view.navigateSearch("last")
        self.assertIn("new alpha", view.textCursor().block().text())
        view.setSearch("alpha", True)
        view.navigateSearch("first")
        self.assertEqual(view.textCursor().selectedText(), "alpha")
        view.setFilters(device="other")
        view.navigateSearch("first")
        self.assertIn("hidden alpha", view.textCursor().block().text())
        position = view.textCursor().position(), view.textCursor().anchor()
        for query in ("absent", ""):
            view.setSearch(query)
            for direction in ("first", "previous", "next", "last"):
                self.assertFalse(view.navigateSearch(direction))
                self.assertEqual((view.textCursor().position(), view.textCursor().anchor()), position)

    def test_current_search_match_has_distinct_rendered_color(self):
        view = self.view()
        view.resize(400, 100)
        view.show()
        settings = self.manager.config.general

        def background_at(position):
            cursor = QTextCursor(view.document())
            cursor.setPosition(position)
            rect = view.cursorRect(cursor)
            return view.viewport().grab().toImage().pixelColor(rect.left() + 2, rect.top() + 1)

        try:
            for theme in ("light", "dark"):
                settings.appearance = theme
                applyAppearance(settings)
                view.setPlainText("Alpha alpha")
                view.setSearch("alpha")
                for direction, selected, other in (("first", 0, 6), ("next", 6, 0), ("next", 0, 6)):
                    view.navigateSearch(direction)
                    for focused in (True, False):
                        with self.subTest(theme=theme, direction=direction, focused=focused):
                            if focused:
                                view.activateWindow()
                                view.setFocus()
                            else:
                                view.clearFocus()
                            APP.processEvents()
                            self.assertEqual(view.textCursor().selectionStart(), selected)
                            self.assertNotEqual(background_at(selected), background_at(other))
                view.setSearch("")
                view.moveCursor(QTextCursor.MoveOperation.End)
                APP.processEvents()
                self.assertEqual(background_at(0), view.palette().base().color())
        finally:
            settings.appearance = "system"
            applyAppearance(settings)

    def test_header_severity_keeps_its_color_over_keywords_and_search(self):
        view = self.view()
        settings = self.manager.config.general
        settings.keywords = ",".join(COLORS["light"])
        view.configure(settings)
        try:
            for mode in ("dark", "light"):
                settings.appearance = mode
                applyAppearance(settings)
                APP.processEvents()
                for level, color in COLORS[mode].items():
                    if level == "section":
                        continue
                    for query in ("", level[:3], f"[{level}]"):
                        with self.subTest(mode=mode, severity=level, search=query):
                            view.clear()
                            text = f"2026-10-29T11:59:15.720+08:00 server APP [{level}] - body {level}"
                            view.appendMessage(text)
                            view.setSearch(query)
                            start = text.index(f"[{level}]")
                            formats = view.document().firstBlock().layout().formats()
                            for position in range(start, start + len(level) + 2):
                                run = next(run for run in formats if run.start <= position < run.start + run.length)
                                self.assertEqual(run.format.foreground().color().name(), color)
                                if start < position < start + len(level) + 1:
                                    self.assertEqual(run.format.fontWeight(), QFont.Weight.Bold)
                                if query and text.find(query) <= position < text.find(query) + len(query):
                                    self.assertEqual(run.format.background().color().name(),
                                                     "#665200" if mode == "dark" else "#ffe082")
                            if not query:
                                body = text.rindex(level)
                                run = next(run for run in formats if run.start <= body < run.start + run.length)
                                self.assertEqual(run.format.foreground().color().name(), COLORS[mode]["section"])
        finally:
            settings.appearance = "system"
            applyAppearance(settings)

    def test_toolbar_statusbar_and_line_number_toggle(self):
        window = self.window()
        self.assertTrue(all(not button.isEnabled() for button in window.search_buttons.values()))
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
        for direction, expected in (("first", "Alpha"), ("next", "alpha"), ("last", "alpha"), ("previous", "Alpha")):
            QTest.mouseClick(window.search_buttons[direction], Qt.MouseButton.LeftButton)
            self.assertEqual(window.log.textCursor().selectedText(), expected)
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
                self.assertEqual(highlighted.format.background().color().name(),
                                 "#665200" if mode == "dark" else "#ffe082")
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
