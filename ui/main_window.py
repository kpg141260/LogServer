__VERSION__ = "0.1.0.14"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from datetime import datetime
from pathlib import Path
import logging
import queue
import time

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton, QSplitter, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from core.app_state_manager import AppStateManager
from core.config_loader import VERSION
from core.messages import SEVERITIES
from core.server import LogServer
from ui.appearance import applyAppearance
from ui.log_file import LogFileLoader, LogFileMerger
from ui.log_view import LogView
from ui.wrappers.preferences_wrapper import PreferencesDialog


class MainWindow(QMainWindow):
    def __init__(self, setup, parent=None):
        super().__init__(parent)
        self.manager = setup.config_manager
        self.state = AppStateManager(str(setup.paths.state_file))
        self.server = None
        self.file_loader = None
        self.merge_worker = None
        self.merge_paths = None
        self._merged_path = None
        self._file_load_cancelled = False
        self._file_error = ""
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
        self.file_log = LogView()
        self.file_log.setAccessibleName("Opened log file")
        self.file_log.setPlaceholderText("Use File > Open log file... to load a .log file.")
        
        file_pane = QWidget()
        self.file_pane = file_pane
        file_layout = QVBoxLayout(file_pane)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.file_label = QLabel("Log file: no file loaded")
        self.file_status = QLabel()

        self.file_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.file_splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        for label in (self.file_label, self.file_status):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            left_layout.addWidget(label)

        self.file_status.hide()
        left_layout.addWidget(self.file_log)
        self.file_splitter.addWidget(left)
        self.file_right_pane = QWidget()
        right_layout = QVBoxLayout(self.file_right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.second_file_label = QLabel()
        self.second_file_status = QLabel()
        for label in (self.second_file_label, self.second_file_status):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            right_layout.addWidget(label)
        self.second_file_log = LogView()
        self.second_file_log.setAccessibleName("Second log file for merging")
        right_layout.addWidget(self.second_file_log)
        self.file_splitter.addWidget(self.file_right_pane)
        self.file_right_pane.hide()
        file_layout.addWidget(self.file_splitter)
        self.merge_status = QLabel()
        self.merge_status.setTextFormat(Qt.TextFormat.PlainText)
        self.merge_status.setWordWrap(True)
        self.merge_status.hide()
        file_layout.addWidget(self.merge_status)
        self.merge_button = QPushButton("&Merge...")
        self.merge_button.clicked.connect(self.saveMergedLog)
        self.merge_button.hide()
        file_layout.addWidget(self.merge_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.log)
        self.splitter.addWidget(file_pane)
        self.splitter.setSizes([300, 250])
        file_pane.hide()
        layout.addWidget(self.splitter)
        
        self.active_log = self.log
        
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
        clear.triggered.connect(lambda: self.active_log.clear())
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        for action in (self.start_action, self.stop_action, clear):
            toolbar.addAction(action)

        self.filter_button = QToolButton()
        self.filter_button.setText("Filter")
        self.filter_button.setAccessibleName("Filter log messages")
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_menu = QMenu(self.filter_button)
        self.filter_menu.aboutToShow.connect(self._populateFilters)
        self.filter_button.setMenu(self.filter_menu)

        toolbar.addWidget(self.filter_button)

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
        self.search_buttons = {}

        for direction, label, icon in (
                ("first", "First match", QStyle.StandardPixmap.SP_MediaSkipBackward),
                ("previous", "Previous match", QStyle.StandardPixmap.SP_ArrowBack),
                ("next", "Next match", QStyle.StandardPixmap.SP_ArrowForward),
                ("last", "Last match", QStyle.StandardPixmap.SP_MediaSkipForward)):
            button = QToolButton()
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setAutoRaise(True)
            button.setEnabled(False)
            button.clicked.connect(lambda checked, direction=direction: self.active_log.navigateSearch(direction))
            search_layout.addWidget(button)
            self.search_buttons[direction] = button
            
        self.statusBar().addPermanentWidget(search_controls)
        find = QAction("Find", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self._focusSearch)
        self.addAction(find)

        menu = self.menuBar().addMenu("&File")
        self.open_log_action = QAction("Open log file...", self)
        self.open_log_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_log_action.triggered.connect(self.openLogFile)
        menu.addAction(self.open_log_action)
        self.merge_log_action = QAction("Merge log files...", self)
        self.merge_log_action.triggered.connect(self.selectMergeFiles)
        menu.addAction(self.merge_log_action)
        self.close_log_action = QAction("Close log file", self)
        self.close_log_action.setEnabled(False)
        self.close_log_action.triggered.connect(self.closeLogFile)
        menu.addAction(self.close_log_action)

        for action in (self.start_action, self.stop_action, preferences, quit_action):
            menu.addAction(action)

        menu_view = self.menuBar().addMenu("&View")
        self.pane_selector = QComboBox()
        self.pane_selector.setAccessibleName("Controls for pane")
        self.pane_selector.addItems(("Socket display", "Log file"))
        self.pane_selector.model().item(1).setEnabled(False)
        pane_action = QWidgetAction(menu_view)
        pane_action.setDefaultWidget(self.pane_selector)

        self.line_numbers_action = QAction("Line numbers", self)
        self.line_numbers_action.setCheckable(True)
        self.line_numbers_action.toggled.connect(self._lineNumbersChanged)

        # menu_view.addAction(clear)
        menu_view.addAction(pane_action)
        menu_view.addAction(self.line_numbers_action)
        
        self.pane_selector.currentIndexChanged.connect(self._paneChanged)
        for index, view in enumerate((self.log, self.file_log, self.second_file_log)):
            view.focused.connect(lambda index=index: self.pane_selector.setCurrentIndex(index))

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
        values = self.active_log.filterValues()
        values["severity"].update(SEVERITIES)
        values["protocol"].update(("TCP", "UDP", "APP"))
        for key, label in (("severity", "Severity"), ("ip", "IP"), ("protocol", "Protocol"), ("device", "Device")):
            menu = self.filter_menu.addMenu(label)
            group = QActionGroup(menu)
            selected = self.active_log.filters[key]
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
        filters = dict(self.active_log.filters)
        filters[key] = value
        self.active_log.setFilters(**filters)
        active = sum(value is not None for value in filters.values())
        self.filter_button.setText(f"Filter ({active})" if active else "Filter")

    def _resetFilters(self):
        self.active_log.setFilters()
        self.filter_button.setText("Filter")

    def _searchChanged(self, *args):
        self.active_log.setSearch(self.search.text(), self.match_case.isChecked())
        self._updateSearchButtons()

    def _updateSearchButtons(self):
        for button in self.search_buttons.values():
            button.setEnabled(bool(self.active_log.search_text))

    def _lineNumbersChanged(self, visible):
        self.active_log.setLineNumbersVisible(visible)

    def _paneChanged(self, index):
        self.active_log = (self.log, self.file_log, self.second_file_log)[index]
        with QSignalBlocker(self.search), QSignalBlocker(self.match_case), QSignalBlocker(self.line_numbers_action):
            self.search.setText(self.active_log.search_text)
            self.match_case.setChecked(self.active_log.match_case)
            self.line_numbers_action.setChecked(not self.active_log.line_numbers.isHidden())
        active = sum(value is not None for value in self.active_log.filters.values())
        self.filter_button.setText(f"Filter ({active})" if active else "Filter")
        self._updateSearchButtons()
        self.active_log.setFocus()

    def _focusSearch(self):
        self.search.setFocus()
        self.search.selectAll()

    def _status(self, state):
        self.statusBar().showMessage(f"{state} | {self.clients} TCP clients | {self.received} messages received")
        self._server_state = state

    def _configurationChanged(self, config):
        applyAppearance(config.general)
        for view in (self.log, self.file_log, self.second_file_log):
            view.configure(config.general)
        logging.getLogger("LogServer").setLevel(config.logging.level.upper())
        if self.server is None:
            settings = config.socket
            self.endpoint.setText(f"{settings.protocol.upper()} | {settings.host}:{settings.port}")
        else:
            self.endpoint.setText("Preferences saved. Stop and Start the server to apply socket and file changes.")

    def openPreferences(self):
        PreferencesDialog(self.manager, self).exec()

    def openLogFile(self):
        dialog = QFileDialog(self, "Open log file", self.manager.config.logging.directory)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setNameFilters(("Log files (*.log)", "All files (*)"))
        dialog.setDefaultSuffix("log")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.loadLogFile(dialog.selectedFiles()[0])
        dialog.deleteLater()

    def closeLogFile(self):
        self._file_load_cancelled = True
        if self.file_loader is not None:
            self.file_loader.requestInterruption()
        if self.merge_worker is not None:
            self.merge_worker.requestInterruption()
        self.pane_selector.setCurrentIndex(0)
        self._leaveMerge()
        self.pane_selector.model().item(1).setEnabled(False)
        self.file_log.clear()
        self.file_label.setText("Log file: no file loaded")
        self.file_status.clear()
        self.file_status.hide()
        self.file_pane.hide()
        self.close_log_action.setEnabled(False)

    def loadLogFile(self, path):
        self._startFileLoad(path, self._fileLoaded)

    def selectMergeFiles(self):
        paths = []
        directory = self.manager.config.logging.directory
        for title in ("Select first log file to merge", "Select second log file to merge"):
            dialog = QFileDialog(self, title, directory)
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            dialog.setNameFilters(("Log files (*.log)", "All files (*)"))
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            selected = dialog.selectedFiles()
            dialog.deleteLater()
            if not accepted:
                return
            paths.append(selected[0])
            directory = str(Path(selected[0]).parent)
        self.loadMergeFiles(paths)

    def loadMergeFiles(self, paths):
        if self.file_loader is not None or self.merge_worker is not None:
            return
        self._file_load_cancelled = False
        if len(paths) != 2:
            self._mergeFailed("Choose exactly two log files to merge.")
            return
        self._startFileLoad(tuple(paths), self._mergeFilesLoaded)

    def _startFileLoad(self, paths, loaded):
        if self.file_loader is not None or self.merge_worker is not None:
            return
        self._file_load_cancelled = False
        self.file_loader = LogFileLoader(paths, self.manager.config.general.max_display_lines, self)
        self.file_loader.loaded.connect(loaded)
        self.file_loader.failed.connect(self._fileLoadFailed)
        self.file_loader.finished.connect(self._fileLoadFinished)
        self.open_log_action.setEnabled(False)
        self.merge_log_action.setEnabled(False)
        self.merge_button.setEnabled(False)
        self.file_status.setText("Loading log files..." if len(self.file_loader.paths) == 2 else
                                 f"Loading {self.file_loader.path.name}...")
        self.file_status.show()
        try:
            self.file_loader.start()
        except RuntimeError as exc:
            self._fileLoadFailed(str(exc))
            self._fileLoadFinished()

    def _fileLoaded(self, result):
        if self._closing or self._file_load_cancelled:
            return
        self._leaveMerge()
        self._setFilePreview(result, self.file_log, self.file_label, self.file_status)
        self._showFilePane()

    def _setFilePreview(self, result, view, label, status):
        path, lines, omitted = result
        limit = self.manager.config.general.max_display_lines
        omitted += max(0, len(lines) - limit)
        lines = lines[-limit:]
        view.setLines(lines)
        label.setText(f"Log file: {path}")
        status.setText(f"Showing the last {len(lines)} lines; {omitted} older lines omitted." if omitted else "")
        status.setVisible(bool(omitted))

    def _showFilePane(self):
        if self.error.text() == self._file_error:
            self.error.clear()
            self.error.hide()
        self._file_error = ""
        if self.file_pane.isHidden():
            self.file_pane.show()
            height = self.splitter.height() // 2
            self.splitter.setSizes([height, height])
        self.pane_selector.model().item(1).setEnabled(True)
        self.close_log_action.setEnabled(True)
        self.pane_selector.setCurrentIndex(1)
        self.file_log.setFocus()

    def _mergeFilesLoaded(self, results):
        if self._closing or self._file_load_cancelled:
            return
        self._setFilePreview(results[0], self.file_log, self.file_label, self.file_status)
        self._setFilePreview(results[1], self.second_file_log, self.second_file_label, self.second_file_status)
        self.file_label.setText(f"File 1: {results[0][0]}")
        self.second_file_label.setText(f"File 2: {results[1][0]}")
        self.merge_paths = tuple(result[0] for result in results)
        if self.pane_selector.count() == 2:
            self.pane_selector.addItem("Log file 2")
        self.pane_selector.setItemText(1, "Log file 1")
        self.file_right_pane.show()
        width = self.file_splitter.width() // 2
        self.file_splitter.setSizes([width, width])
        self.merge_status.clear()
        self.merge_status.hide()
        self.merge_button.show()
        self._showFilePane()

    def _leaveMerge(self):
        self.merge_paths = None
        if self.pane_selector.currentIndex() == 2:
            self.pane_selector.setCurrentIndex(1)
        if self.pane_selector.count() == 3:
            self.pane_selector.removeItem(2)
        self.pane_selector.setItemText(1, "Log file")
        self.second_file_log.clear()
        self.second_file_label.clear()
        self.second_file_status.clear()
        self.file_right_pane.hide()
        self.merge_button.hide()
        self.merge_status.clear()
        self.merge_status.hide()

    def saveMergedLog(self):
        if self.merge_paths is None or self.merge_worker is not None or self.file_loader is not None:
            return
        dialog = QFileDialog(self, "Save merged log file", str(Path(self.merge_paths[0]).parent / "merged.log"))
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilters(("Log files (*.log)", "All files (*)"))
        dialog.setDefaultSuffix("log")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mergeLogFiles(dialog.selectedFiles()[0])
        dialog.deleteLater()

    def mergeLogFiles(self, destination):
        if self.merge_paths is None or self.merge_worker is not None or self.file_loader is not None:
            return
        self._merged_path = None
        self.merge_worker = LogFileMerger(self.merge_paths, destination, self)
        self.merge_worker.merged.connect(self._logsMerged)
        self.merge_worker.failed.connect(self._mergeFailed)
        self.merge_worker.finished.connect(self._mergeFinished)
        self.open_log_action.setEnabled(False)
        self.merge_log_action.setEnabled(False)
        self.merge_button.setEnabled(False)
        self.merge_status.setText("Merging full log files by timestamp...")
        self.merge_status.show()
        try:
            self.merge_worker.start()
        except RuntimeError as exc:
            self._mergeFailed(str(exc))
            self._mergeFinished()

    def _logsMerged(self, result):
        if self._closing or self._file_load_cancelled:
            return
        path, count = result
        self._merged_path = path
        self.merge_status.setText(f"Merged {count} entries into {path}")
        self.merge_status.show()

    def _mergeFailed(self, error):
        if self._closing or self._file_load_cancelled:
            return
        logging.getLogger("LogServer").warning("Cannot merge log files: %s", error)
        self.merge_status.setText(f"Cannot merge log files: {error}")
        self.merge_status.show()
        if self.file_pane.isHidden():
            self._file_error = self.merge_status.text()
            self.error.setText(self._file_error)
            self.error.show()

    def _mergeFinished(self):
        worker, self.merge_worker = self.merge_worker, None
        path, self._merged_path = self._merged_path, None
        worker.deleteLater()
        self.open_log_action.setEnabled(True)
        self.merge_log_action.setEnabled(True)
        self.merge_button.setEnabled(self.merge_paths is not None)
        if self._closing:
            self.close()
        elif path is not None and not self._file_load_cancelled:
            self.loadLogFile(path)

    def _fileLoadFailed(self, error):
        if self._closing or self._file_load_cancelled:
            return
        path = self.file_loader.path
        logging.getLogger("LogServer").warning("Cannot load log file %s: %s", path, error)
        self.file_status.setText(f"Cannot load {path}: {error}")
        self.file_status.show()
        if self.file_pane.isHidden():
            self._file_error = self.file_status.text()
            self.error.setText(self._file_error)
            self.error.show()

    def _fileLoadFinished(self):
        loader, self.file_loader = self.file_loader, None
        loader.deleteLater()
        self.open_log_action.setEnabled(True)
        self.merge_log_action.setEnabled(True)
        self.merge_button.setEnabled(self.merge_paths is not None)
        if self._closing:
            self.close()

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
        workers = [worker for worker in (self.file_loader, self.merge_worker) if worker is not None]
        if workers:
            self._closing = True
            for worker in workers:
                worker.requestInterruption()
            if self.server is None:
                event.ignore()
                return
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
