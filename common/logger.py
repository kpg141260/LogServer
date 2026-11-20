"""Append-only device logs with optional rotation and bounded open files."""
from __future__ import annotations
__VERSION__ = "0.1.0.3"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import hashlib
import logging
import sys
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import LoggingSettings
from core.log_format import formatFileMessage
from core.messages import LogMessage


class CheckedFileHandler(logging.FileHandler):
    def handleError(self, record):
        raise OSError("Cannot write received log message; check the log folder and available disk space.")


class CheckedRotatingHandler(RotatingFileHandler):
    def handleError(self, record):
        raise OSError("Cannot write or rotate received log; check permissions and available disk space.")


class MessageFileWriter:
    def __init__(self, settings: LoggingSettings):
        self.settings = settings
        self.directory = Path(settings.directory).expanduser().resolve()
        self.handlers = OrderedDict()
        if settings.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)
            if settings.mode == "summary":
                self._handler(settings.summary_file)

    def _handler(self, filename: str):
        if filename in self.handlers:
            self.handlers.move_to_end(filename)
            return self.handlers[filename]
        if len(self.handlers) >= 64:
            _, old = self.handlers.popitem(last=False)
            old.close()
        path = self.directory / filename
        if self.settings.rotate:
            handler = CheckedRotatingHandler(path, mode="a", encoding="utf-8",
                                             maxBytes=self.settings.max_bytes,
                                             backupCount=self.settings.backup_count)
        else:
            handler = CheckedFileHandler(path, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.handlers[filename] = handler
        return handler

    def write(self, message: LogMessage) -> None:
        if not self.settings.enabled:
            return
        filename = self.settings.summary_file
        if self.settings.mode == "client":
            # File identity follows the socket IP, never a device-provided name.
            identity = hashlib.sha256(message.client.encode("utf-8")).hexdigest()[:12]
            filename = f"client-{identity}.log"
        record = logging.LogRecord("devices", logging.INFO, "", 0, formatFileMessage(message, self.settings), (), None)
        self._handler(filename).emit(record)

    def close(self) -> None:
        failure = None
        for handler in self.handlers.values():
            try:
                handler.close()
            except OSError as exc:
                failure = exc
        self.handlers.clear()
        if failure is not None:
            raise failure


class DiagnosticHandler(RotatingFileHandler):
    def handleError(self, record):
        sys.stderr.write("Cannot write application.log; check permissions and available disk space.\n")


class ErrorLogger:
    def __init__(self, config, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("LogServer")
        self.logger.setLevel(config.logging.level.upper())
        self.logger.propagate = False
        self.handler = DiagnosticHandler(directory / "application.log", mode="a", encoding="utf-8",
                                           maxBytes=1048576, backupCount=3)
        self.handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        self.logger.addHandler(self.handler)

    def shutdown(self):
        self.logger.removeHandler(self.handler)
        self.handler.close()
