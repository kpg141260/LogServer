"""OS-specific paths and the isolated first-launch setup process."""
from __future__ import annotations
__VERSION__ = "0.1.0.4"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileDevice, QIODevice, QSaveFile, QStandardPaths

from core.app_state_manager import AppStateManager
from core.config import ConfigManager


@dataclass(frozen=True)
class StartupPaths:
    data_dir: Path
    config_dir: Path
    state_file: Path

    def configPath(self) -> Path:
        return self.config_dir / "logserver.toml"


def startupPaths() -> StartupPaths:
    locations = QStandardPaths.StandardLocation

    def directory(location):
        value = QStandardPaths.writableLocation(location)
        if not value:
            raise OSError("The standard application folder is unavailable.")
        return Path(value) / "LogServer"

    return StartupPaths(directory(locations.GenericDataLocation),
                        directory(locations.GenericConfigLocation),
                        directory(locations.GenericStateLocation) / "logserver.state")


def setupConfiguration(paths: StartupPaths) -> ConfigManager:
    manager = ConfigManager(paths.configPath())
    if not paths.configPath().exists() and not manager.config.logging.directory:
        manager.config.logging.directory = str(paths.data_dir / "logs")
    return manager


def setupReason(paths: StartupPaths) -> str:
    if not AppStateManager(str(paths.state_file)).setupComplete():
        return "Complete startup setup to continue."
    try:
        if not paths.configPath().is_file():
            return "Configuration is missing. Complete startup setup to continue."
        manager = setupConfiguration(paths)
        manager.validate(manager.config)
    except (OSError, ValueError, TypeError) as exc:
        return f"Startup settings need attention: {exc}"
    return ""


def firstLaunch(paths: StartupPaths) -> bool:
    return bool(setupReason(paths))


def installDesktopLauncher() -> Path:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Applications menu launchers are only supported on Linux.")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    if not data_home.is_absolute():
        raise ValueError("XDG_DATA_HOME must be an absolute path.")
    root = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).absolute()
    command = [executable]
    if not getattr(sys, "frozen", False):
        command.append(root / "log_server.py")
    icon = root / "assets" / "logserver.svg"
    for path in (*command, icon):
        if not path.is_file():
            raise OSError(f"Launcher file is unavailable: {path}")
        if any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
            raise ValueError("Launcher paths cannot contain control characters.")
    if any(char in str(executable) for char in "=%"):
        raise ValueError("The launcher executable path cannot contain '=' or '%'.")

    def quote(path):
        value = str(path).replace("%", "%%")
        value = "".join("\\" + char if char in '\\"`$' else char for char in value)
        return '"' + value + '"'

    # Desktop-entry string escaping is applied before Exec argument quoting.
    command_line = " ".join(quote(path) for path in command).replace("\\", "\\\\")
    icon_path = str(icon).replace("\\", "\\\\")
    data = ("[Desktop Entry]\nType=Application\nName=LogServer\n"
            "Comment=Receive and view TCP and UDP device logs\n"
            f"Exec={command_line}\nIcon={icon_path}\n"
            "Terminal=false\nCategories=Utility;\nStartupNotify=true\n"
            "StartupWMClass=LogServer\n").encode("utf-8")
    destination = data_home / "applications" / "logserver.desktop"
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = QSaveFile(str(destination))
    try:
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        permissions = QFileDevice.Permission
        if not output.setPermissions(permissions.ReadOwner | permissions.WriteOwner |
                                     permissions.ReadGroup | permissions.ReadOther):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
    finally:
        if output.isOpen():
            output.cancelWriting()
    return destination


def completeSetup(paths: StartupPaths, manager: ConfigManager, *, create_launcher: bool = False) -> None:
    manager.validate(manager.config)
    if manager.config.logging.enabled:
        Path(manager.config.logging.directory).expanduser().mkdir(parents=True, exist_ok=True)
    state = AppStateManager(str(paths.state_file))
    state.saveSetup(False)
    manager.commit(manager.createDraft())
    if create_launcher:
        installDesktopLauncher()
    state.saveSetup(True)


def ensureStartup(paths: StartupPaths, *, force: bool = False) -> None:
    if not force and not setupReason(paths):
        return
    root = Path(__file__).resolve().parents[1]
    command = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, str(root / "log_server.py")])
    result = subprocess.run([*command, "--setup", str(paths.data_dir),
                             str(paths.config_dir), str(paths.state_file)], check=False)
    if result.returncode == 2:
        raise SystemExit(0)
    if result.returncode:
        raise RuntimeError("Startup setup did not finish successfully.")
    reason = setupReason(paths)
    if reason:
        raise RuntimeError(reason)
