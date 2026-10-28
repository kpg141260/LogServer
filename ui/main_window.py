__VERSION__ = "0.1.0.1"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from datetime import datetime
import logging
import queue
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar, QVBoxLayout, QWidget

from core.app_state_manager import AppStateManager
from core.config_loader import VERSION
from core.server import LogServer
from ui.appearance import applyAppearance
from ui.log_view import LogView
from ui.wrappers.preferences_wrapper import PreferencesDialog


class MainWindow(QMainWindow):
    def __init__(self, setup, parent=None):
        super().__init__(parent)
        self.manager = setup.config_manager
        self.state = AppStateManager(str(setup.paths.state_file))
        self.server = None
        self.received = 0
        self.clients = 0
        self._closing = False
        self._stop_time = None
        self.setWindowTitle(f"LogServer {VERSION}")
        self.resize(1000, 650)
        self.setMinimumSize(500, 300)
        central = QWidget()
        layout = QVBoxLayout(central)
        self.endpoint = QLabel()
        self.endpoint.setWordWrap(True)
        layout.addWidget(self.endpoint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setAccessibleName("Server error")
        self.error.hide()
        layout.addWidget(self.error)
        self.log = LogView()
        layout.addWidget(self.log)
        self.setCentralWidget(central)
        toolbar = QToolBar("Server")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.start_action = QAction("Start", self)
        self.start_action.triggered.connect(self.startServer)
        self.stop_action = QAction("Stop", self)
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stopServer)
        preferences = QAction("Preferences...", self)
        preferences.setShortcut(QKeySequence("Ctrl+,"))
        preferences.triggered.connect(self.openPreferences)
        clear = QAction("Clear display", self)
        clear.triggered.connect(self.log.clear)
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        for action in (self.start_action, self.stop_action, preferences, clear):
            toolbar.addAction(action)
        menu = self.menuBar().addMenu("&File")
        for action in (self.start_action, self.stop_action, preferences, clear, quit_action):
            menu.addAction(action)
        self.manager.configurationChanged.connect(self._configurationChanged)
        self._configurationChanged(self.manager.config)
        if self.manager.config.general.persist:
            self.state.restoreWindow(self)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._drain)
        self.timer.start()
        self._status("Stopped")
        if self.manager.config.general.auto_start:
            QTimer.singleShot(0, self.startServer)

    def _status(self, state):
        self.statusBar().showMessage(f"{state} | {self.clients} TCP clients | {self.received} messages received")
        self._server_state = state

    def _configurationChanged(self, config):
        applyAppearance(config.general)
        self.log.configure(config.general)
        logging.getLogger("LogServer").setLevel(config.logging.level.upper())
        if self.server is None:
            settings = config.socket
            self.endpoint.setText(f"{settings.protocol.upper()} | {settings.host}:{settings.port}")
        else:
            self.endpoint.setText("Preferences saved. Stop and Start the server to apply socket and file changes.")

    def openPreferences(self):
        PreferencesDialog(self.manager, self).exec()

    def startServer(self):
        if self.server is not None:
            return
        self.error.clear()
        self.error.hide()
        self.clients = 0
        try:
            self.server = LogServer(self.manager.config)
            self.server.start()
        except (OSError, ValueError, RuntimeError) as exc:
            self.server = None
            self._notice("ERROR", str(exc))
            return
        self.start_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self._status("Starting")

    def stopServer(self):
        if self.server is not None:
            self.server.stop()
            self._stop_time = time.monotonic()
            self.stop_action.setEnabled(False)
            self._status("Stopping")

    def _notice(self, level, text):
        logger = logging.getLogger("LogServer")
        logger.log(logging.WARNING if level == "WARNING" else logging.ERROR, "%s", text)
        stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self.log.appendMessage(f"{stamp} [server] [APP] {level} - {text}")
        self.error.setText(text)
        self.error.show()

    def _drain(self):
        server = self.server
        if server is None:
            return
        for _ in range(200):
            try:
                kind, value = server.events.get_nowait()
            except queue.Empty:
                break
            if kind == "message":
                self.received += 1
                self.log.appendMessage(value.line)
            elif kind == "clients":
                self.clients = value
            elif kind == "started":
                settings = server.config.socket
                self.endpoint.setText(f"Listening: {settings.protocol.upper()} | {value[0]}:{value[1]}")
                self._server_state = "Listening"
            elif kind in ("warning", "error"):
                self._notice(kind.upper(), value)
        self._status(self._server_state)
        if not server.thread.is_alive() and server.events.empty():
            self.clients = 0
            if server.failure and self.error.text() != server.failure:
                self._notice("ERROR", server.failure)
            self.server = None
            self.start_action.setEnabled(True)
            self.stop_action.setEnabled(False)
            self._stop_time = None
            self._status("Stopped")
            if self._closing:
                self.close()
        elif self._stop_time is not None and time.monotonic() - self._stop_time > 5:
            self.error.setText("Waiting for log writes to finish. Check whether the log storage is available.")
            self.error.show()
            self._stop_time = None

    def closeEvent(self, event):
        if self.server is not None:
            self._closing = True
            self.start_action.setEnabled(False)
            self.stopServer()
            event.ignore()
            return
        if self.manager.config.general.persist:
            self.state.saveWindow(self)
        self.timer.stop()
        event.accept()
