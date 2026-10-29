__VERSION__ = "0.1.0.10"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
"""NetworkMonitor-style severity coloring with keyword and section highlights."""
import re

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QSyntaxHighlighter, QTextBlockUserData, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from core.messages import LEVEL
from ui.appearance import logColors


HEADER = re.compile(r"^\d{4}-\d\d-\d\dT\S+ (?P<ip>\S+) (?P<protocol>TCP|UDP|APP) \[(?P<severity>[A-Z]+)\] (?:- )?")
DEVICE = re.compile(r"\|([^|\n]*)\|")
TIMESTAMP = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
DEVICE_HEADER = re.compile(
    rf"{TIMESTAMP}[ \t]+"
    rf"(?:\[[ \t]*{LEVEL.pattern}[ \t]*\]|{LEVEL.pattern})[ \t]*(?:[-:][ \t]*)?", re.I)
IP_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
TOKENS = re.compile(
    rf"(?P<time>\b{TIMESTAMP}\b)"
    rf"|(?P<ip>(?<![\w.]){IP_OCTET}(?:\.{IP_OCTET}){{3}}(?![\w.]))"
    r"|(?P<number>(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.]))")
SECTION = re.compile(r"\^[^^\n]+\^")
COLORS = {
    "light": {"DEBUG": "#2e1b6c", 
              "INFO": "#2f5e27", 
              "NOTICE": "#2f5e27",
              "WARNING": "#8a5f15", 
              "ERROR": "#5d1b1b", 
              "CRITICAL": "#a10000",
              "ALERT": "#a10000", 
              "EMERGENCY": "#a10000", 
              "section": "#193062"},
    "dark": {"DEBUG": "#5d37da", 
             "INFO": "#6ddb5a", 
             "NOTICE": "#6ddb5a",
             "WARNING": "#ffb027", 
             "ERROR": "#ee4646", 
             "CRITICAL": "#ff0000",
             "ALERT": "#ff0000", 
             "EMERGENCY": "#ff0000", 
             "section": "#417dff"},
}


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document, view):
        super().__init__(document)
        self.view = view
        self.keywords = None
        self.search = None

    def configure(self, keywords):
        words = [word.strip() for word in keywords.split(",") if word.strip()]
        self.keywords = re.compile("|".join(re.escape(word) for word in words), re.I) if words else None
        self.rehighlight()

    def highlightBlock(self, text):
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))

        def apply(start, end, style):
            self.setFormat(offsets[start], offsets[end] - offsets[start], style)

        dark = self.view.palette().base().color().lightness() < 128
        colors = COLORS["dark" if dark else "light"]
        header = HEADER.match(text)
        levels = ("DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")
        state = levels.index(header["severity"]) if header and header["severity"] in levels else max(0, self.previousBlockState())
        self.setCurrentBlockState(state)
        style = QTextCharFormat()
        style.setForeground(QColor(colors[levels[state]]))
        apply(0, len(text), style)
        token_colors = logColors(dark)
        for match in TOKENS.finditer(text):
            token_style = QTextCharFormat()
            token_style.setForeground(QColor(token_colors[match.lastgroup]))
            apply(match.start(), match.end(), token_style)
        if header:
            emphasis = QTextCharFormat(style)
            emphasis.setFontWeight(QFont.Weight.Bold)
            apply(header.start("severity"), header.end("severity"), emphasis)
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
                highlight.setBackground(self.view.palette().highlight())
                highlight.setForeground(self.view.palette().highlightedText())
                apply(match.start(), match.end(), highlight)


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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("Received log messages")
        self.filters = dict.fromkeys(("severity", "ip", "protocol", "device"))
        self.line_numbers = LineNumberArea(self)
        self.line_numbers.hide()
        self.blockCountChanged.connect(self._updateLineNumberAreaWidth)
        self.updateRequest.connect(self._updateLineNumberArea)
        self.highlighter = LogHighlighter(self.document(), self)

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
        fields = dict.fromkeys(self.filters, "")
        if header:
            fields.update(header.groupdict())
            device = DEVICE.match(text, header.end())
            fields["device"] = device[1] if device else ""
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

    def _filterBlock(self, block):
        data = block.userData()
        fields = data.fields if data else {}
        visible = all(value is None or fields.get(key) == value for key, value in self.filters.items())
        block.setVisible(visible)
        block.setLineCount(max(1, block.layout().lineCount()) if visible else 0)

    def setFilters(self, severity=None, ip=None, protocol=None, device=None):
        self.filters = {"severity": severity, "ip": ip, "protocol": protocol, "device": device}
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
        self.highlighter.search = re.compile(re.escape(text), 0 if match_case else re.I) if text else None
        self.highlighter.rehighlight()

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
