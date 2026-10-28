__VERSION__ = "0.1.0.2"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from pathlib import Path

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from core.startup import completeSetup, setupConfiguration, setupReason
from ui.wrappers.preferences_wrapper import PreferencesDialog


class StartupDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.manager = None
        self.setWindowTitle("LogServer - Startup setup")
        self.resize(650, 380)
        root = QVBoxLayout(self)
        instructions = QLabel("Confirm where to keep received logs. Configuration and window state use your operating system's application folders. Save and Continue creates missing folders and saves setup.")
        instructions.setWordWrap(True)
        root.addWidget(instructions)
        form = QFormLayout()
        root.addLayout(form)
        for title, path in (("Configuration", paths.configPath()), ("Window state", paths.state_file)):
            label = QLabel(str(path))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(title, label)
        self.directory = QLineEdit(str(paths.data_dir / "logs"))
        row = QHBoxLayout()
        row.addWidget(self.directory)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form.addRow("Received log &folder", row)
        self.directory.setAccessibleName("Received log folder")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        preferences = QPushButton("Socket and other preferences...")
        preferences.clicked.connect(self._preferences)
        root.addWidget(preferences)
        self.error = QLabel(setupReason(paths))
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save and Continue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        try:
            self.manager = setupConfiguration(paths)
            self.directory.setText(self.manager.config.logging.directory)
            self._summary()
        except (OSError, ValueError, TypeError) as exc:
            self.error.setText(f"Cannot load configuration: {exc}\nCorrect the configuration file and reopen setup.")
            buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            preferences.setEnabled(False)

    def _summary(self):
        settings = self.manager.config.socket
        self.summary.setText(f"Listen on {settings.host}:{settings.port} ({settings.protocol.upper()}).\nUse 0.0.0.0 in Preferences to accept IPv4 devices on your network.")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Received log folder", self.directory.text())
        if folder:
            self.directory.setText(folder)

    def _preferences(self):
        value = self.directory.text().strip()
        self.manager.config.logging.directory = str(Path(value).expanduser().resolve()) if value else ""
        if PreferencesDialog(self.manager, self).exec() == QDialog.DialogCode.Accepted:
            self.directory.setText(self.manager.config.logging.directory)
            self._summary()

    def accept(self):
        if self.manager is None:
            return
        value = self.directory.text().strip()
        self.manager.config.logging.directory = str(Path(value).expanduser().resolve()) if value else ""
        try:
            completeSetup(self.paths, self.manager)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            self.error.setText(f"Setup could not finish: {exc}")
            return
        super().accept()
