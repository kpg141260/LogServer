__VERSION__ = "0.1.0.3"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.log_merge import mergeLogFiles
from core.messages import plainText
from ui.log_view import logFields


class LogFileLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path, limit, parent=None):
        super().__init__(parent)
        self.paths = tuple(Path(item) for item in path) if isinstance(path, (tuple, list)) else (Path(path),)
        self.path = self.paths[0]
        self.limit = limit

    def run(self):
        try:
            results = []
            for path in self.paths:
                self.path = path
                result = self._readFile()
                if self.isInterruptionRequested():
                    return
                results.append(result)
            self.loaded.emit(results[0] if len(results) == 1 else results)
        except (OSError, UnicodeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            logging.getLogger("LogServer").exception("Unexpected failure reading %s", self.path)
            self.failed.emit(f"Unexpected error: {exc}")

    def _readFile(self):
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
        return str(self.path), list(lines), total - len(lines)


class LogFileMerger(QThread):
    merged = Signal(object)
    failed = Signal(str)

    def __init__(self, paths, destination, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.destination = str(destination)

    def run(self):
        try:
            count = mergeLogFiles(self.paths, self.destination, self.isInterruptionRequested)
            self.merged.emit((self.destination, count))
        except InterruptedError:
            return
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            logging.getLogger("LogServer").exception("Unexpected failure merging log files")
            self.failed.emit(f"Unexpected error: {exc}")
