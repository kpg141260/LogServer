import logging
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout


class HelpDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle("LogServer help")
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName("LogServer help")
        layout.addWidget(self.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def loadHelp(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            logging.getLogger("LogServer").warning("Cannot read help file %s: %s", self.path, exc)
            self.browser.setPlainText(f"Cannot read the help file.\n{self.path}\n\n{exc}")
            return
        self.browser.setMarkdown(text)
