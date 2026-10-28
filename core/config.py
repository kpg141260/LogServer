"""Validated application preferences and atomic TOML persistence."""
from __future__ import annotations
__VERSION__ = "0.1.0.8"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import copy
import ipaddress
import json
import math
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QSaveFile, Signal


@dataclass
class GeneralSettings:
    appearance: str = "system"
    widget_style: str = ""
    log_font: str = ""
    interface_font: str = ""
    persist: bool = True
    auto_start: bool = True
    max_display_lines: int = 10000
    keywords: str = "error,critical,warning,failed,success"


@dataclass
class SocketSettings:
    host: str = "127.0.0.1"
    port: int = 5514
    protocol: str = "tcp"
    backlog: int = 32
    max_clients: int = 100
    idle_timeout: float = 300.0
    receive_buffer: int = 65536
    max_message_bytes: int = 65536
    encoding: str = "utf-8"
    keepalive: bool = True


@dataclass
class LoggingSettings:
    enabled: bool = True
    directory: str = ""
    mode: str = "summary"
    summary_file: str = "devices.log"
    rotate: bool = True
    max_bytes: int = 10485760
    backup_count: int = 5
    level: str = "info"


@dataclass
class ApplicationConfig:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    socket: SocketSettings = field(default_factory=SocketSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    def to_mapping(self) -> dict:
        result = asdict(self)
        result["settings"] = result.pop("general")
        return result


ConfigDraft = ApplicationConfig


def validate(config: ApplicationConfig) -> None:
    defaults = ApplicationConfig()
    for section in ("general", "socket", "logging"):
        actual, expected = getattr(config, section), getattr(defaults, section)
        for item in fields(expected):
            value, default = getattr(actual, item.name), getattr(expected, item.name)
            if isinstance(default, float):
                valid = type(value) in (int, float) and math.isfinite(value)
            else:
                valid = type(value) is type(default)
            if not valid:
                raise ValueError(f"{section}.{item.name} has an invalid type or value.")
    if config.general.appearance not in ("system", "dark", "light"):
        raise ValueError("Appearance must be system, dark or light.")
    if not 100 <= config.general.max_display_lines <= 1000000:
        raise ValueError("Display limit must be between 100 and 1000000 lines.")
    if len(config.general.keywords) > 4096:
        raise ValueError("Highlight keywords must fit within 4096 characters.")
    try:
        ipaddress.ip_address(config.socket.host)
    except ValueError as exc:
        raise ValueError("Bind address must be an IPv4 or IPv6 address.") from exc
    if config.socket.protocol not in ("tcp", "udp", "both"):
        raise ValueError("Protocol must be tcp, udp or both.")
    for key, low, high in (("port", 1, 65535), ("backlog", 1, 1024),
                           ("max_clients", 1, 256), ("receive_buffer", 1024, 1048576),
                           ("max_message_bytes", 256, 1048576)):
        if not low <= getattr(config.socket, key) <= high:
            raise ValueError(f"Socket {key} must be between {low} and {high}.")
    if not 0 <= config.socket.idle_timeout <= 86400:
        raise ValueError("Idle timeout must be between 0 and 86400 seconds (0 disables it).")
    if config.socket.encoding not in ("utf-8", "ascii", "latin-1"):
        raise ValueError("Encoding must be utf-8, ascii or latin-1.")
    log = config.logging
    if log.mode not in ("summary", "client"):
        raise ValueError("File mode must be summary or client.")
    if log.level not in ("debug", "info", "warning", "error", "critical"):
        raise ValueError("Invalid application log level.")
    if not 1024 <= log.max_bytes <= 1073741824 or not 1 <= log.backup_count <= 100:
        raise ValueError("Rotation requires 1024-1073741824 bytes and 1-100 backups.")
    if log.enabled and not log.directory.strip():
        raise ValueError("Choose a folder for received logs.")
    if not log.summary_file or log.summary_file in (".", "..") or any(
        ord(char) < 32 or char in '/\\<>:"|?*' for char in log.summary_file
    ) or log.summary_file.endswith((".", " ")):
        raise ValueError("Summary file must be a file name without path separators or reserved characters.")
    stem = log.summary_file.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("Summary file name is reserved on Windows.")
    for value in (log.directory, config.general.log_font, config.general.interface_font):
        if "\x00" in value:
            raise ValueError("Settings must not contain null characters.")


class ConfigManager(QObject):
    configurationChanged = Signal(object)

    def __init__(self, config_path: str | Path, overrides: dict | None = None, parent=None):
        super().__init__(parent)
        self.config_path = Path(config_path).expanduser().resolve()
        try:
            with self.config_path.open("rb") as handle:
                mapping = tomllib.load(handle)
        except FileNotFoundError:
            mapping = {}
        self.config = ApplicationConfig()
        for section, attribute in (("settings", "general"), ("socket", "socket"), ("logging", "logging")):
            values = mapping.get(section, {})
            if not isinstance(values, dict):
                raise ValueError(f"{section} must be a table.")
            values = {**values, **(overrides or {}).get(section, {})}
            target = getattr(self.config, attribute)
            allowed = {item.name for item in fields(target)}
            unknown = set(values) - allowed
            if unknown:
                raise ValueError(f"Unknown {section} settings: {', '.join(sorted(unknown))}")
            for key, value in values.items():
                setattr(target, key, value)

    def createDraft(self) -> ConfigDraft:
        return copy.deepcopy(self.config)

    def validate(self, draft: ConfigDraft) -> None:
        validate(draft)
        if draft.logging.enabled and draft.logging.mode == "summary":
            target = Path(draft.logging.directory).expanduser() / draft.logging.summary_file
            if target.resolve() == self.config_path:
                raise ValueError("The received log file must differ from the configuration file.")

    def commit(self, draft: ConfigDraft) -> None:
        self.validate(draft)
        lines = []
        for section, values in draft.to_mapping().items():
            lines.append(f"[{section}]")
            for key, value in values.items():
                literal = str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)
                lines.append(f"{key} = {literal}")
            lines.append("")
        data = "\n".join(lines).encode("utf-8")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        output = QSaveFile(str(self.config_path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
        self.config = copy.deepcopy(draft)
        self.configurationChanged.emit(self.config)
