"""OS-specific paths and the isolated first-launch setup process."""
from __future__ import annotations
__VERSION__ = "0.1.0.4"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths

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


def completeSetup(paths: StartupPaths, manager: ConfigManager) -> None:
    manager.validate(manager.config)
    if manager.config.logging.enabled:
        Path(manager.config.logging.directory).expanduser().mkdir(parents=True, exist_ok=True)
    state = AppStateManager(str(paths.state_file))
    state.saveSetup(False)
    manager.commit(manager.createDraft())
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
