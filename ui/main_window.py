__VERSION__ = "0.1.0.7"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from datetime import datetime
import logging
import queue
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QToolBar, QToolButton, QVBoxLayout, QWidget

from core.app_state_manager import AppStateManager
from core.config_loader import VERSION
from core.messages import SEVERITIES
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
        self.filter_button = QToolButton()
        self.filter_button.setText("Filter")
        self.filter_button.setAccessibleName("Filter log messages")
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_menu = QMenu(self.filter_button)
        self.filter_menu.aboutToShow.connect(self._populateFilters)
        self.filter_button.setMenu(self.filter_menu)
        toolbar.addWidget(self.filter_button)
        self.line_numbers_action = QAction("Line numbers", self)
        self.line_numbers_action.setCheckable(True)
        self.line_numbers_action.toggled.connect(self.log.setLineNumbersVisible)
        toolbar.addAction(self.line_numbers_action)
        search_controls = QWidget()
        search_layout = QHBoxLayout(search_controls)
        search_layout.setContentsMargins(search_layout.spacing(), 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search log text")
        self.search.setAccessibleName("Search log text")
        self.search.setClearButtonEnabled(True)
        search_label = QLabel("&Search:")
        search_label.setBuddy(self.search)
        self.match_case = QCheckBox("Match case")
        self.search.textChanged.connect(self._searchChanged)
        self.match_case.toggled.connect(self._searchChanged)
        for widget in (search_label, self.search, self.match_case):
            search_layout.addWidget(widget)
        self.statusBar().addPermanentWidget(search_controls)
        find = QAction("Find", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self._focusSearch)
        self.addAction(find)
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

    def _populateFilters(self):
        for action in self.filter_menu.actions():
            if action.menu():
                action.menu().deleteLater()
        self.filter_menu.clear()
        values = self.log.filterValues()
        values["severity"].update(SEVERITIES)
        values["protocol"].update(("TCP", "UDP", "APP"))
        for key, label in (("severity", "Severity"), ("ip", "IP"), ("protocol", "Protocol"), ("device", "Device")):
            menu = self.filter_menu.addMenu(label)
            group = QActionGroup(menu)
            selected = self.log.filters[key]
            if selected is not None:
                values[key].add(selected)
            for value in (None, *sorted(values[key])):
                title = "All" if value is None else value or ("(No device)" if key == "device" else "(Unknown)")
                action = menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(value == selected)
                group.addAction(action)
                action.triggered.connect(lambda checked, key=key, value=value: self._setFilter(key, value))
        self.filter_menu.addSeparator()
        self.filter_menu.addAction("Reset filters", self._resetFilters)

    def _setFilter(self, key, value):
        filters = dict(self.log.filters)
        filters[key] = value
        self.log.setFilters(**filters)
        active = sum(value is not None for value in filters.values())
        self.filter_button.setText(f"Filter ({active})" if active else "Filter")

    def _resetFilters(self):
        self.log.setFilters()
        self.filter_button.setText("Filter")

    def _searchChanged(self, *args):
        self.log.setSearch(self.search.text(), self.match_case.isChecked())

    def _focusSearch(self):
        self.search.setFocus()
        self.search.selectAll()

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
        self.log.appendMessage(f"{stamp} server APP [{level}] - {text}")
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
                self.log.appendMessage(value.line, value)
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
