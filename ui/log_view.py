__VERSION__ = "0.1.0.18"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
"""NetworkMonitor-style severity coloring with keyword and section highlights."""
import re
import time
from bisect import bisect_left
from itertools import chain

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QSyntaxHighlighter, QTextBlockUserData, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from core.messages import LEVEL, parseMessage
from core.log_format import DEVICE_HEADER, FORMATTED_HEADER, TIMESTAMP
from ui.appearance import logColors
from ui.log_search import SearchWorker


HEADER = re.compile(r"^\d{4}-\d\d-\d\dT\S+ \[?(?P<ip>[^\s\]]+)\]? \[?(?P<protocol>TCP|UDP|APP)\]? \[?(?P<severity>[A-Z]+)\]? (?:- )?")
DEVICE = re.compile(r"\|([^|\n]*)\|")
IP_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
PATH = (
    r'''(?<=["'])(?:[^"'\n]*[\\/][^"'\n]+|[^"'\n]*\.[A-Za-z][\w.-]*)(?=["'])'''
    r'''|(?<![\w/\\])(?:[A-Za-z]:[\\/]|~?[\\/]|\.{1,2}[\\/]|[\w.-]+[\\/])'''
    r'''[^\s"'<>|,;!?()\[\]{}]*[^\s"'<>|,;:!?()\[\]{}.]'''
    r"|(?<![\w.])(?:[\w-]+\.)+[A-Za-z][\w-]*(?![\w.])"
    r"|(?<![\w.])\.[A-Za-z][\w-]*(?![\w.])"
)
TOKENS = re.compile(
    rf"(?P<time>\b{TIMESTAMP}\b)"
    rf"|(?P<ip>(?<![\w.]){IP_OCTET}(?:\.{IP_OCTET}){{3}}(?![\w.]))"
    rf"|(?P<path>{PATH})"
    r"|(?P<number>(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.]))")
SECTION = re.compile(r"\^[^^\n]+\^")
FILE_HEADER = re.compile(
    rf"^(?:{TIMESTAMP}[ \t]+)?(?:\[[ \t]*)?(?P<severity>{LEVEL.pattern})[ \t]*\]?(?:[ \t]+|[-:][ \t]*|$)", re.IGNORECASE)
COLORS = {
    "light": {"DEBUG": "#2b2b2b", 
              "INFO": "#4d983f", 
              "NOTICE": "#4d983f",
              "WARNING": "#b57a1c", 
              "ERROR": "#912a2a", 
              "CRITICAL": "#be0000",
              "ALERT": "#be0000", 
              "EMERGENCY": "#be0000", 
              "section": "#2b52a8"},
    "dark": {"DEBUG": "#b0b0b0", 
             "INFO": "#6ddb5a", 
             "NOTICE": "#6ddb5a",
             "WARNING": "#ffb027", 
             "ERROR": "#ee4646", 
             "CRITICAL": "#ff0000",
             "ALERT": "#ff0000", 
             "EMERGENCY": "#ff0000", 
             "section": "#417dff"},
}


def logFields(text, previous=None):
    fields = dict.fromkeys(("severity", "ip", "protocol", "device"), "")
    header = HEADER.match(text) or FORMATTED_HEADER.match(text)
    if header:
        fields.update({key: value or "" for key, value in header.groupdict().items()})
        device = DEVICE.match(text, header.end())
        fields["device"] = device[1] if device else ""
    elif text.startswith(("{", "<")):
        message = parseMessage(text.encode("utf-8", errors="replace"), ("", 0), "", "utf-8")
        fields.update(severity=message.level, device=message.device)
    else:
        header = FILE_HEADER.match(text)
        if header:
            level = header["severity"].upper()
            fields["severity"] = {"WARN": "WARNING", "FATAL": "CRITICAL"}.get(level, level)
        elif previous is not None:
            return previous
    return fields


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document, view):
        super().__init__(document)
        self.view = view
        self.keywords = None
        self.search = None

    def configure(self, keywords):
        words = [word.strip() for word in keywords.split(",") if word.strip()]
        self.keywords = re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE) if words else None
        self.rehighlight()

    def highlightBlock(self, text):
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))

        def apply(start, end, style):
            self.setFormat(offsets[start], offsets[end] - offsets[start], style)

        dark = self.view.palette().base().color().lightness() < 128
        colors = COLORS["dark" if dark else "light"]
        header = HEADER.match(text) or FILE_HEADER.match(text) or FORMATTED_HEADER.match(text)
        levels = ("DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")
        data = self.currentBlock().userData()
        level = header["severity"] if header else data.fields["severity"] if data else ""
        level = {"WARN": "WARNING", "FATAL": "CRITICAL"}.get(level.upper(), level.upper())
        state = levels.index(level) if level in levels else max(0, self.previousBlockState())
        self.setCurrentBlockState(state)
        style = QTextCharFormat()
        style.setForeground(QColor(colors[levels[state]]))
        apply(0, len(text), style)
        token_colors = logColors(dark)

        for match in TOKENS.finditer(text):
            token_style = QTextCharFormat()
            token_style.setForeground(QColor(token_colors[match.lastgroup]))
            apply(match.start(), match.end(), token_style)

        emphasis = QTextCharFormat()
        emphasis.setForeground(QColor(colors["section"]))
        emphasis.setFontWeight(QFont.Weight.Bold)

        for pattern in (self.keywords, SECTION):
            if pattern:
                for match in pattern.finditer(text):
                    apply(match.start(), match.end(), emphasis)

        if self.search:
            for match in self.search.finditer(text):
                highlight = QTextCharFormat(self.format(offsets[match.start()]))
                highlight.setBackground(QColor("#665200" if dark else "#ffe082"))
                highlight.setForeground(self.view.palette().text())
                apply(match.start(), match.end(), highlight)

        if header:
            start, end = header.start("severity"), header.end("severity")
            label_start = start - int(start > 0 and text[start - 1] == "[")
            label_end = end + int(end < len(text) and text[end] == "]")
            for position in range(label_start, label_end):
                emphasis = QTextCharFormat(self.format(offsets[position]))
                emphasis.setForeground(style.foreground())
                if start <= position < end:
                    emphasis.setFontWeight(QFont.Weight.Bold)
                apply(position, position + 1, emphasis)


class LogBlockData(QTextBlockUserData):
    def __init__(self, fields):
        super().__init__()
        self.fields = fields


class LineNumberArea(QWidget):
    def sizeHint(self):
        return QSize(self.parent().lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.parent().paintLineNumbers(event)


class LogView(QPlainTextEdit):
    focused = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("Received log messages")
        self.filters = dict.fromkeys(("severity", "ip", "protocol", "device", "keywords"))
        self._keyword_pattern = None
        self.search_text = ""
        self.match_case = False
        self._search_generation = 0
        self._search_worker = None
        self._search_matches = None
        self._pending_navigation = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._startSearch)
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._highlightSearchBatch)
        self._highlight_block = None
        self.line_numbers = LineNumberArea(self)
        self.line_numbers.hide()
        self.blockCountChanged.connect(self._updateLineNumberAreaWidth)
        self.updateRequest.connect(self._updateLineNumberArea)
        self.highlighter = LogHighlighter(self.document(), self)
        self.document().contentsChange.connect(self._searchContentsChanged)

    def configure(self, settings):
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if settings.log_font:
            font.fromString(settings.log_font)
        self.setFont(font)
        self.document().setMaximumBlockCount(settings.max_display_lines)
        self.highlighter.configure(settings.keywords)
        self._updateLineNumberAreaWidth()

    def appendMessage(self, text, message=None):
        header = HEADER.match(text)
        fields = logFields(text)
        if header:
            device = DEVICE.match(text, header.end())
            body_start = device.end() if device else header.end()
            device_header = DEVICE_HEADER.match(text, body_start)
            if device_header:
                text = text[:header.end()].removesuffix("- ") + text[header.end():body_start] + text[device_header.end():]
        if message is not None:
            fields = {"severity": message.level, "ip": message.client,
                      "protocol": message.transport.upper(), "device": message.device}
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 1
        old_value = bar.value()
        selection = self.textCursor()
        position, anchor = selection.position(), selection.anchor()
        before = self.document().characterCount()
        separator = not self.document().isEmpty()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(text)
        block = cursor.block()
        first_modified = block.position()
        for _ in range(text.count("\n") + text.count("\u2029") + 1):
            if not block.isValid():
                break
            block.setUserData(LogBlockData(fields))
            self._filterBlock(block)
            first_modified = block.position()
            block = block.previous()
        if any(value is not None for value in self.filters.values()):
            self.document().markContentsDirty(first_modified, self.document().characterCount() - first_modified)
        added = len(text.encode("utf-16-le")) // 2 + int(separator)
        removed = max(0, before + added - self.document().characterCount())
        selection.setPosition(max(0, anchor - removed))
        selection.setPosition(max(0, position - removed), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)
        bar.setValue(bar.maximum() if at_end else old_value)

    def setLines(self, lines):
        limit = self.document().maximumBlockCount()
        if limit:
            lines = lines[-limit:]
        self.setPlainText("\n".join(text for text, fields in lines))
        block = self.document().firstBlock()
        for text, fields in lines:
            if not block.isValid():
                break
            block.setUserData(LogBlockData(fields))
            self._filterBlock(block)
            block = block.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.highlighter.rehighlight()
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def _filterBlock(self, block):
        data = block.userData()
        fields = data.fields if data else {}
        visible = all(value is None or fields.get(key) == value for key, value in self.filters.items() if key != "keywords")
        if visible and self._keyword_pattern:
            visible = self._keyword_pattern.search(block.text()) is not None
        block.setVisible(visible)
        block.setLineCount(max(1, block.layout().lineCount()) if visible else 0)

    def setFilters(self, severity=None, ip=None, protocol=None, device=None, keywords=None):
        words = [word.strip() for word in (keywords or "").split(",") if word.strip()]
        self._keyword_pattern = re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE) if words else None
        self.filters = {"severity": severity, "ip": ip, "protocol": protocol, "device": device,
                        "keywords": keywords if words else None}
        block = self.document().firstBlock()
        while block.isValid():
            self._filterBlock(block)
            block = block.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update()
        self.line_numbers.update()

    def filterValues(self):
        values = {key: set() for key in self.filters}
        block = self.document().firstBlock()
        while block.isValid():
            data = block.userData()
            if data:
                for key, value in data.fields.items():
                    values[key].add(value)
            block = block.next()
        return values

    def setSearch(self, text, match_case=False):
        self.search_text, self.match_case = text, match_case
        self.highlighter.search = re.compile(re.escape(text), 0 if match_case else re.IGNORECASE) if text else None
        self._pending_navigation = None
        self._invalidateSearch()
        self._highlight_block = self.document().firstBlock()
        self._highlightSearchBatch()

    def _invalidateSearch(self, delay=120):
        self._search_generation += 1
        self._search_matches = None
        if self._search_worker is not None:
            self._search_worker.cancelled.set()
            self._search_worker = None
        self._search_timer.stop()
        if self.search_text and self.blockCount() > 500:
            self._search_timer.start(delay)

    def _searchContentsChanged(self, position, removed, added):
        if removed or added:
            self._invalidateSearch(0)

    def _highlightSearchBatch(self):
        self._highlight_timer.stop()
        deadline = time.monotonic() + 0.004
        block = self._highlight_block
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        try:
            while block is not None and block.isValid():
                self.highlighter.rehighlightBlock(block)
                block = block.next()
                if time.monotonic() >= deadline:
                    break
        finally:
            cursor.endEditBlock()
        self._highlight_block = block
        if block is not None and block.isValid():
            self._highlight_timer.start(0)

    def _startSearch(self):
        if not self.highlighter.search:
            return
        worker = SearchWorker(self._search_generation, self.toPlainText(), self.highlighter.search)
        worker.signals.finished.connect(self._searchFinished)
        self._search_worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(int, object)
    def _searchFinished(self, generation, matches):
        if generation != self._search_generation:
            return
        self._search_worker = None
        self._search_matches = matches if matches is not None else []
        if self._pending_navigation is not None:
            direction = self._pending_navigation
            self._pending_navigation = None
            self.navigateSearch(direction)

    def navigateSearch(self, direction):
        pattern = self.highlighter.search
        if pattern is None:
            return False
        if self.blockCount() > 500:
            return self._navigateIndexedSearch(direction)
        current = self.textCursor()
        boundary = current.selectionStart() if direction == "previous" else current.selectionEnd()
        first = last = target = None
        block = self.document().firstBlock()
        while block.isValid():
            if block.isVisible():
                text = block.text()
                offsets = [0]
                for char in text:
                    offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
                for match in pattern.finditer(text):
                    span = (block.position() + offsets[match.start()], block.position() + offsets[match.end()])
                    if first is None:
                        first = span
                    last = span
                    if direction == "first" or direction == "next" and span[0] >= boundary:
                        target = span
                        break
                    if direction == "previous" and span[1] <= boundary:
                        target = span
            if target is not None and direction in ("first", "next"):
                break
            block = block.next()
        if direction == "last" or direction == "previous" and target is None:
            target = last
        elif direction == "next" and target is None:
            target = first
        if target is None:
            return False
        cursor = QTextCursor(self.document())
        cursor.setPosition(target[0])
        cursor.setPosition(target[1], QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return True

    def _navigateIndexedSearch(self, direction):
        matches = self._search_matches
        if matches is None:
            self._pending_navigation = direction
            return False
        current = self.textCursor()
        boundary = current.selectionStart() if direction == "previous" else current.selectionEnd()
        if direction == "first":
            indices = range(len(matches))
        elif direction == "last":
            indices = range(len(matches) - 1, -1, -1)
        else:
            pivot = bisect_left(matches, (boundary,))
            if direction == "previous":
                while pivot and matches[pivot - 1][1] > boundary:
                    pivot -= 1
                indices = chain(range(pivot - 1, -1, -1), range(len(matches) - 1, pivot - 1, -1))
            else:
                indices = chain(range(pivot, len(matches)), range(pivot))
        for index in indices:
            start, end = matches[index]
            if not self.document().findBlock(start).isVisible():
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            return True
        return False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()

    def setLineNumbersVisible(self, visible):
        self.line_numbers.setVisible(visible)
        self._updateLineNumberAreaWidth()

    def lineNumberAreaWidth(self):
        if self.line_numbers.isHidden():
            return 0
        return self.fontMetrics().horizontalAdvance("9" * len(str(self.blockCount()))) + self.fontMetrics().horizontalAdvance(" ") * 2

    def _updateLineNumberAreaWidth(self, *args):
        width = self.lineNumberAreaWidth()
        self.setViewportMargins(width, 0, 0, 0)
        rect = self.contentsRect()
        self.line_numbers.setGeometry(QRect(rect.left(), rect.top(), width, rect.height()))
        self.line_numbers.update()

    def _updateLineNumberArea(self, rect, dy):
        if dy:
            self.line_numbers.scroll(0, dy)
        else:
            self.line_numbers.update(0, rect.y(), self.line_numbers.width(), rect.height())

    def paintLineNumbers(self, event):
        painter = QPainter(self.line_numbers)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        block = self.firstVisibleBlock()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top > event.rect().bottom():
                break
            if block.isVisible():
                painter.drawText(0, round(top), self.line_numbers.width() - self.fontMetrics().horizontalAdvance(" "),
                                 self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            block = block.next()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateLineNumberAreaWidth()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "highlighter"):
            self.highlighter.rehighlight()
            self.line_numbers.update()
        if event.type() == QEvent.Type.FontChange and hasattr(self, "line_numbers"):
            self._updateLineNumberAreaWidth()
