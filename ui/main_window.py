__VERSION__ = "0.1.0.14"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from datetime import datetime
from pathlib import Path
import logging
import queue
import time

from PySide6.QtCore import QEvent, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QStyle, QTabBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget

from common.vt import VersionInfo
from core.app_state_manager import AppStateManager
from core.config_loader import VERSION
from core.messages import SEVERITIES
from core.server import LogServer
from ui.appearance import applyAppearance
from ui.help_dialog import HelpDialog
from ui.log_file import LogFileLoader, LogFileMerger
from ui.log_view import COLORS, LogView
from ui.wrappers.preferences_wrapper import PreferencesDialog


class MainWindow(QMainWindow):
    def __init__(self, setup, parent=None):
        super().__init__(parent)
        self.manager = setup.config_manager
        self.state = AppStateManager(str(setup.paths.state_file))
        self.server = None
        self.file_loader = None
        self.merge_worker = None
        self.help_dialog = None
        self.merge_paths = None
        self._merged_path = None
        self._file_load_cancelled = False
        self._file_error = ""
        self.received = 0
        self.clients = 0
        self.client_logs = {}
        self.connected_clients = set()
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
        self.socket_pane = QWidget()
        socket_layout = QVBoxLayout(self.socket_pane)
        socket_layout.setContentsMargins(0, 0, 0, 0)
        socket_layout.addWidget(QLabel("Socket logs"))
        self.socket_tabs = QTabWidget()
        self.socket_tabs.setAccessibleName("Client logs")
        self.socket_tabs.setTabsClosable(True)
        self.socket_tabs.setUsesScrollButtons(True)
        self.socket_tabs.addTab(self.log, "All clients")
        for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
            self.socket_tabs.tabBar().setTabButton(0, side, None)
        self.socket_tabs.tabCloseRequested.connect(self._closeClientTab)
        socket_layout.addWidget(self.socket_tabs)
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
        self.splitter.addWidget(self.socket_pane)
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
        self.clear_action = clear
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
            
        status_controls = QWidget()
        status_layout = QHBoxLayout(status_controls)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel()
        self.log_level_label = QLabel()
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.log_level_label)
        self.statusBar().addWidget(status_controls)
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
        self.show_socket_action = QAction("Socket panel", self)
        self.show_socket_action.setCheckable(True)
        self.show_socket_action.setChecked(True)
        self.show_socket_action.toggled.connect(lambda visible: self._setPanelVisible(self.socket_pane, visible))
        self.show_file_action = QAction("File panel", self)
        self.show_file_action.setCheckable(True)
        self.show_file_action.toggled.connect(lambda visible: self._setPanelVisible(self.file_pane, visible))
        menu_view.addAction(self.show_socket_action)
        menu_view.addAction(self.show_file_action)
        menu_view.addSeparator()
        self.pane_selector = QComboBox(self)
        self.pane_selector.hide()
        self.pane_selector.setAccessibleName("Controls for pane")
        self.pane_selector.addItems(("Socket display", "Log file"))
        self.pane_selector.model().item(1).setEnabled(False)

        self.line_numbers_action = QAction("Line numbers", self)
        self.line_numbers_action.setCheckable(True)
        self.line_numbers_action.toggled.connect(self._lineNumbersChanged)

        # menu_view.addAction(clear)
        menu_view.addAction(self.line_numbers_action)

        menu_help = self.menuBar().addMenu("&Help")
        help_action = menu_help.addAction("Help", self.openHelp)
        help_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        about_action = menu_help.addAction("About", self.openAbout)
        about_action.setMenuRole(QAction.MenuRole.NoRole)
        
        self.pane_selector.currentIndexChanged.connect(self._paneChanged)
        self.socket_tabs.currentChanged.connect(self._socketTabChanged)
        self.log.focused.connect(lambda: self._socketViewFocused(self.log))
        for index, view in enumerate((self.file_log, self.second_file_log), start=1):
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

    def openHelp(self):
        if self.help_dialog is None:
            self.help_dialog = HelpDialog(Path(__file__).resolve().parent.parent / "assets" / "help.md", self)
        self.help_dialog.loadHelp()
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def openAbout(self):
        try:
            info = VersionInfo()
            version = info.version() if info.project_info else "unavailable"
        except (OSError, ValueError, TypeError, AttributeError):
            logging.getLogger("LogServer").warning("Cannot read application version", exc_info=True)
            version = "unavailable"
        QMessageBox.about(self, "About LogServer", f"LogServer {version}\n\n"
                          "TCP and UDP log receiver with live client tabs, log-file viewing and timestamp-ordered merging.\n\n"
                          "Built with Python and PySide6.\n\n"
                          "Copyright (c) 2026 Peter Gossler\nMIT License")

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
        self.filter_menu.addAction("Keyword...", self._keywordFilter)
        self.filter_menu.addSeparator()
        self.filter_menu.addAction("Reset filters", self._resetFilters)

    def _keywordFilter(self):
        view = self.active_log
        text, accepted = QInputDialog.getText(self, "Keyword filter", "Keywords (comma-separated, match any):",
                                             text=view.filters["keywords"] or "")
        if accepted:
            self._setFilter("keywords", text.strip() or None)

    def _setFilter(self, key, value):
        filters = dict(self.active_log.filters)
        filters[key] = value
        self.active_log.setFilters(**filters)
        active = sum(value is not None for value in self.active_log.filters.values())
        self.filter_button.setText(f"Filter ({active})" if active else "Filter")

    def _resetFilters(self):
        self.active_log.setFilters()
        self.filter_button.setText("Filter")

    def _searchChanged(self, *args):
        self.active_log.setSearch(self.search.text(), self.match_case.isChecked())
        self._updateSearchButtons()

    def _updateSearchButtons(self):
        for button in self.search_buttons.values():
            button.setEnabled(self.search.isEnabled() and bool(self.active_log.search_text))

    def _lineNumbersChanged(self, visible):
        self.active_log.setLineNumbersVisible(visible)

    def _paneChanged(self, index):
        if index < 0 or not self.pane_selector.model().item(index).isEnabled():
            return
        self.active_log = (self.socket_tabs.currentWidget(), self.file_log, self.second_file_log)[index]
        with QSignalBlocker(self.search), QSignalBlocker(self.match_case), QSignalBlocker(self.line_numbers_action):
            self.search.setText(self.active_log.search_text)
            self.match_case.setChecked(self.active_log.match_case)
            self.line_numbers_action.setChecked(not self.active_log.line_numbers.isHidden())
        active = sum(value is not None for value in self.active_log.filters.values())
        self.filter_button.setText(f"Filter ({active})" if active else "Filter")
        self._updateSearchButtons()
        self.active_log.setFocus()

    def _setPanelVisible(self, panel, visible):
        panel.setVisible(visible)
        socket_visible = not self.socket_pane.isHidden()
        file_visible = not self.file_pane.isHidden()
        self.pane_selector.model().item(0).setEnabled(socket_visible)
        for index in range(1, self.pane_selector.count()):
            self.pane_selector.model().item(index).setEnabled(file_visible)
        enabled = socket_visible or file_visible
        for control in (self.pane_selector, self.filter_button, self.search, self.match_case,
                        self.line_numbers_action, self.clear_action):
            control.setEnabled(enabled)
        index = self.pane_selector.currentIndex()
        if enabled and not self.pane_selector.model().item(index).isEnabled():
            self.pane_selector.setCurrentIndex(0 if socket_visible else 1)
        elif enabled:
            self._paneChanged(index)
        self._updateSearchButtons()
        if socket_visible and file_visible:
            height = self.splitter.height() // 2
            self.splitter.setSizes([height, height])

    def _socketTabChanged(self, index):
        if index >= 0 and not self.socket_pane.isHidden():
            if self.pane_selector.currentIndex() == 0:
                self._paneChanged(0)
            else:
                self.pane_selector.setCurrentIndex(0)

    def _socketViewFocused(self, view):
        self.socket_tabs.setCurrentWidget(view)
        self.pane_selector.setCurrentIndex(0)

    def _clientLog(self, key):
        if key not in self.client_logs:
            view = LogView()
            view.configure(self.manager.config.general)
            view.focused.connect(lambda: self._socketViewFocused(view))
            self.client_logs[key] = view
            self.socket_tabs.addTab(view, "")
            self._updateClientTab(key)
        return self.client_logs[key]

    def _updateClientTab(self, key):
        transport, address, port = key
        address = f"[{address}]" if ":" in address else address
        state = "Connected" if key in self.connected_clients else "Disconnected"
        title = f"{transport.upper()} {address}:{port}"
        if transport == "tcp":
            title += f" ({state})"
        view = self.client_logs[key]
        view.setAccessibleName(title)
        self.socket_tabs.setTabText(self.socket_tabs.indexOf(view), title)

    def _clientConnectionChanged(self, peer, connected):
        key = ("tcp", peer[0], peer[1])
        if connected:
            self.connected_clients.add(key)
            self._clientLog(key)
        else:
            self.connected_clients.discard(key)
        if key in self.client_logs:
            self._updateClientTab(key)

    def _closeClientTab(self, index):
        view = self.socket_tabs.widget(index)
        if view is None or view is self.log:
            return
        key = next(key for key, item in self.client_logs.items() if item is view)
        del self.client_logs[key]
        self.socket_tabs.removeTab(index)
        view.deleteLater()

    def _focusSearch(self):
        self.search.setFocus()
        self.search.selectAll()

    def _status(self, state):
        self.status_label.setText(f"{state} | {self.clients} TCP clients | {self.received} messages received |")
        self._server_state = state

    def _updateLogLevel(self):
        level = self.manager.config.logging.level
        dark = self.palette().base().color().lightness() < 128
        color = COLORS["dark" if dark else "light"].get(level.upper(), COLORS["dark" if dark else "light"]["DEBUG"])
        self.log_level_label.setText(level.lower())
        self.log_level_label.setStyleSheet(f"color: {color};")

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "log_level_label"):
            self._updateLogLevel()

    def _configurationChanged(self, config):
        applyAppearance(config.general)
        for view in (self.log, *self.client_logs.values(), self.file_log, self.second_file_log):
            view.configure(config.general)
        logging.getLogger("LogServer").setLevel(config.logging.level.upper())
        self._updateLogLevel()
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
        self._leaveMerge()
        self.file_log.clear()
        self.file_label.setText("Log file: no file loaded")
        self.file_status.clear()
        self.file_status.hide()
        self.show_file_action.setChecked(False)
        self._setPanelVisible(self.file_pane, False)
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
            self.show_file_action.setChecked(True)
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
                key = (value.transport.lower(), value.client, value.port)
                self._clientLog(key).appendMessage(value.line, value)
            elif kind in ("connected", "disconnected"):
                self._clientConnectionChanged(value, kind == "connected")
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
            for key in tuple(self.connected_clients):
                self._clientConnectionChanged(key[1:], False)
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
        if self.help_dialog is not None:
            self.help_dialog.close()
        self.timer.stop()
        event.accept()
