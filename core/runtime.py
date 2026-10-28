__VERSION__ = "0.1.0.2"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from dataclasses import dataclass

from core.config import ConfigManager
from core.startup import StartupPaths


@dataclass
class ApplicationSetup:
    config_manager: ConfigManager
    paths: StartupPaths
