from __future__ import annotations
__VERSION__ = "0.1.0.7"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFontDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QStyleFactory, QTabWidget, QVBoxLayout, QWidget,
)

from core.config import ConfigManager


class PreferencesDialog(QDialog):
    def __init__(self, manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.draft = manager.createDraft()
        self.setWindowTitle("Preferences")
        self.resize(640, 570)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self._appearance()
        self._socket()
        self._logging()
        note = QLabel("Socket and file changes apply when the server is next started. Display changes apply immediately.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setAccessibleName("Preferences error")
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _tab(self, title):
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.tabs.addTab(page, title)
        return form

    @staticmethod
    def _combo(values, current):
        box = QComboBox()
        for text, value in values:
            box.addItem(text, value)
        box.setCurrentIndex(max(0, box.findData(current)))
        return box

    @staticmethod
    def _spin(low, high, value):
        box = QSpinBox()
        box.setRange(low, high)
        box.setValue(value)
        return box

    @staticmethod
    def _check(value):
        box = QCheckBox()
        box.setChecked(value)
        return box

    def _appearance(self):
        settings = self.draft.general
        form = self._tab("Appearance and startup")
        self.appearance = self._combo((("Follow OS", "system"), ("Dark", "dark"), ("Light", "light")), settings.appearance)
        form.addRow("&Appearance", self.appearance)
        self.style = self._combo([("OS default", ""), *((style, style) for style in QStyleFactory.keys())], settings.widget_style)
        form.addRow("Widget &style", self.style)
        for name, label in (("log_font", "Log font"), ("interface_font", "Interface font")):
            button = QPushButton("Choose font...")
            button.clicked.connect(lambda checked=False, key=name, target=button: self._font(key, target))
            form.addRow(label, button)
        self.persist = self._check(settings.persist)
        form.addRow("Restore window &geometry", self.persist)
        self.auto_start = self._check(settings.auto_start)
        form.addRow("Start &listening on launch", self.auto_start)
        self.lines = self._spin(100, 1000000, settings.max_display_lines)
        form.addRow("Display line li&mit", self.lines)
        self.keywords = QLineEdit(settings.keywords)
        self.keywords.setMaxLength(4096)
        form.addRow("Highlight &keywords (comma separated)", self.keywords)
        label = QLabel("Use ^section^ in device messages to emphasize a section. Severity names are colored automatically.")
        label.setWordWrap(True)
        form.addRow(label)

    def _font(self, key, button):
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont) if key == "log_font" else self.font()
        value = getattr(self.draft.general, key)
        if value:
            font.fromString(value)
        accepted, chosen = QFontDialog.getFont(font, self, "Choose font")
        if accepted:
            setattr(self.draft.general, key, chosen.toString())
            button.setText(f"{chosen.family()}, {chosen.pointSize()} pt")

    def _socket(self):
        settings = self.draft.socket
        form = self._tab("Socket")
        self.host = QLineEdit(settings.host)
        form.addRow("&Bind address (IPv4 / IPv6)", self.host)
        self.port = self._spin(1, 65535, settings.port)
        form.addRow("&Port", self.port)
        self.protocol = self._combo((("TCP", "tcp"), ("UDP", "udp"), ("TCP and UDP", "both")), settings.protocol)
        form.addRow("P&rotocol", self.protocol)
        self.encoding = self._combo([(value, value) for value in ("utf-8", "ascii", "latin-1")], settings.encoding)
        form.addRow("&Encoding", self.encoding)
        self.backlog = self._spin(1, 1024, settings.backlog)
        form.addRow("TCP listen &backlog", self.backlog)
        self.clients = self._spin(1, 256, settings.max_clients)
        form.addRow("Ma&ximum TCP clients", self.clients)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0, 86400)
        self.timeout.setValue(settings.idle_timeout)
        self.timeout.setSuffix(" s")
        self.timeout.setSpecialValueText("Disabled")
        form.addRow("TCP &idle timeout", self.timeout)
        self.buffer = self._spin(1024, 1048576, settings.receive_buffer)
        form.addRow("Receive b&uffer (bytes)", self.buffer)
        self.message_size = self._spin(256, 1048576, settings.max_message_bytes)
        form.addRow("Maximum &message (bytes)", self.message_size)
        self.keepalive = self._check(settings.keepalive)
        form.addRow("TCP &keepalive", self.keepalive)
        note = QLabel("127.0.0.1 accepts local clients; 0.0.0.0 accepts IPv4 devices on your network. TCP uses newline-delimited messages. UDP accepts one message per datagram.")
        note.setWordWrap(True)
        form.addRow(note)

    def _logging(self):
        settings = self.draft.logging
        form = self._tab("Files")
        self.logging_enabled = self._check(settings.enabled)
        form.addRow("&Write received messages to files", self.logging_enabled)
        self.directory = QLineEdit(settings.directory)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.directory)
        row.addWidget(browse)
        form.addRow("Log &folder", row)
        self.directory.setAccessibleName("Log folder")
        self.mode = self._combo((("One combined file", "summary"), ("One file per client IP", "client")), settings.mode)
        form.addRow("File &mode", self.mode)
        self.filename = QLineEdit(settings.summary_file)
        form.addRow("Combined file &name", self.filename)
        self.rotate = self._check(settings.rotate)
        form.addRow("&Rotate files", self.rotate)
        self.max_bytes = self._spin(1024, 1073741824, settings.max_bytes)
        form.addRow("Ma&ximum file size (bytes)", self.max_bytes)
        self.backups = self._spin(1, 100, settings.backup_count)
        form.addRow("&Backup count", self.backups)
        self.log_level = self._combo([(value.upper(), value) for value in ("debug", "info", "warning", "error", "critical")], settings.level)
        form.addRow("&Application diagnostic level", self.log_level)
        note = QLabel("All received severities are saved. Existing files are appended. Rotation retains the configured number of backups. Devices sharing an IP share a client log.")
        note.setWordWrap(True)
        form.addRow(note)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Received log folder", self.directory.text())
        if folder:
            self.directory.setText(folder)

    def accept(self):
        general, socket, logging = self.draft.general, self.draft.socket, self.draft.logging
        general.appearance = self.appearance.currentData()
        general.widget_style = self.style.currentData()
        general.persist = self.persist.isChecked()
        general.auto_start = self.auto_start.isChecked()
        general.max_display_lines = self.lines.value()
        general.keywords = self.keywords.text()
        socket.host, socket.port = self.host.text().strip(), self.port.value()
        socket.protocol, socket.encoding = self.protocol.currentData(), self.encoding.currentData()
        socket.backlog, socket.max_clients = self.backlog.value(), self.clients.value()
        socket.idle_timeout, socket.receive_buffer = self.timeout.value(), self.buffer.value()
        socket.max_message_bytes, socket.keepalive = self.message_size.value(), self.keepalive.isChecked()
        logging.enabled, logging.directory = self.logging_enabled.isChecked(), self.directory.text().strip()
        logging.mode, logging.summary_file = self.mode.currentData(), self.filename.text().strip()
        logging.rotate, logging.max_bytes = self.rotate.isChecked(), self.max_bytes.value()
        logging.backup_count, logging.level = self.backups.value(), self.log_level.currentData()
        if logging.directory:
            logging.directory = str(Path(logging.directory).expanduser().resolve())
        try:
            self.manager.commit(self.draft)
        except (OSError, ValueError, TypeError) as exc:
            self.error.setText(f"Cannot save preferences: {exc}")
            return
        super().accept()
