__VERSION__ = "0.1.0.3"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
"""NetworkMonitor-style severity coloring with keyword and section highlights."""
import re

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit


HEADER = re.compile(r"^\d{4}-\d\d-\d\dT\S+ \[[^\]]+\] \[(?:TCP|UDP|APP)\] ([A-Z]+) - ")
SECTION = re.compile(r"\^[^^\n]+\^")
COLORS = {
    "light": {"DEBUG": "#52606d", "INFO": "#125c32", "NOTICE": "#125c32",
              "WARNING": "#895000", "ERROR": "#b51d22", "CRITICAL": "#a00072",
              "ALERT": "#a00072", "EMERGENCY": "#a00072", "section": "#005b9a"},
    "dark": {"DEBUG": "#adbac7", "INFO": "#8ddb9a", "NOTICE": "#8ddb9a",
             "WARNING": "#ffd166", "ERROR": "#ff9292", "CRITICAL": "#ffa5e3",
             "ALERT": "#ffa5e3", "EMERGENCY": "#ffa5e3", "section": "#83d4ff"},
}


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document, view):
        super().__init__(document)
        self.view = view
        self.keywords = None

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
        state = levels.index(header[1]) if header and header[1] in levels else max(0, self.previousBlockState())
        self.setCurrentBlockState(state)
        style = QTextCharFormat()
        style.setForeground(QColor(colors[levels[state]]))
        apply(0, len(text), style)
        if header:
            emphasis = QTextCharFormat(style)
            emphasis.setFontWeight(QFont.Weight.Bold)
            apply(header.start(1), header.end(1), emphasis)
        emphasis = QTextCharFormat()
        emphasis.setForeground(QColor(colors["section"]))
        emphasis.setFontWeight(QFont.Weight.Bold)
        for pattern in (self.keywords, SECTION):
            if pattern:
                for match in pattern.finditer(text):
                    apply(match.start(), match.end(), emphasis)


class LogView(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setAccessibleName("Received log messages")
        self.highlighter = LogHighlighter(self.document(), self)

    def configure(self, settings):
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if settings.log_font:
            font.fromString(settings.log_font)
        self.setFont(font)
        self.document().setMaximumBlockCount(settings.max_display_lines)
        self.highlighter.configure(settings.keywords)

    def appendMessage(self, text):
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
        added = len(text.encode("utf-16-le")) // 2 + int(separator)
        removed = max(0, before + added - self.document().characterCount())
        selection.setPosition(max(0, anchor - removed))
        selection.setPosition(max(0, position - removed), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)
        bar.setValue(bar.maximum() if at_end else old_value)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "highlighter"):
            self.highlighter.rehighlight()
