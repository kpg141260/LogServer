__VERSION__ = "0.1.0.3"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.messages import plainText
from ui.log_view import logFields


class LogFileLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path, limit, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.limit = limit

    def run(self):
        try:
            lines = deque(maxlen=self.limit)
            total = 0
            fields = None
            if not self.path.is_file():
                raise OSError("Choose an existing regular log file.")
            with self.path.open("r", encoding="utf-8-sig", errors="replace") as source:
                for raw in source:
                    if self.isInterruptionRequested():
                        return
                    for text in plainText(raw.rstrip("\n")).split("\u2029"):
                        fields = logFields(text, fields)
                        lines.append((text, fields))
                        total += 1
            if not self.isInterruptionRequested():
                self.loaded.emit((str(self.path), list(lines), total - len(lines)))
        except (OSError, UnicodeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            logging.getLogger("LogServer").exception("Unexpected failure reading %s", self.path)
            self.failed.emit(f"Unexpected error: {exc}")
