import logging
import threading

from PySide6.QtCore import QObject, QRunnable, Signal


class SearchSignals(QObject):
    finished = Signal(int, object)


class SearchWorker(QRunnable):
    def __init__(self, generation, text, pattern):
        super().__init__()
        self.generation = generation
        self.text = text
        self.pattern = pattern
        self.cancelled = threading.Event()
        self.signals = SearchSignals()

    def run(self):
        try:
            matches = []
            offset = position = 0
            for match in self.pattern.finditer(self.text):
                if self.cancelled.is_set():
                    return
                offset += len(self.text[position:match.start()].encode("utf-16-le")) // 2
                end = offset + len(match[0].encode("utf-16-le")) // 2
                matches.append((offset, end))
                position, offset = match.end(), end
            if not self.cancelled.is_set():
                self.signals.finished.emit(self.generation, matches)
        except Exception:
            logging.getLogger("LogServer").exception("Unexpected failure searching log text")
            self.signals.finished.emit(self.generation, None)
