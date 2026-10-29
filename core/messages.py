"""Plain, JSON and syslog messages normalized for safe display and files."""
from __future__ import annotations
__VERSION__ = "0.1.0.5"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import json
import re
from dataclasses import dataclass
from datetime import datetime


ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
LEVEL = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|NOTICE|ALERT|EMERGENCY)\b", re.I)
PRIORITY = re.compile(r"^<(\d{1,3})>")
SEVERITIES = ("EMERGENCY", "ALERT", "CRITICAL", "ERROR", "WARNING", "NOTICE", "INFO", "DEBUG")


def plainText(text: str) -> str:
    text = ANSI.sub("", text)
    return "".join(char if char in "\n\t" or (ord(char) >= 32 and ord(char) != 127)
                   else "" for char in text).replace("\r", "")


@dataclass(frozen=True)
class LogMessage:
    received: str
    client: str
    port: int
    transport: str
    level: str
    device: str
    text: str
    raw: str

    @property
    def line(self) -> str:
        prefix = f"{self.received} {self.client} {self.transport.upper()} [{self.level}] - |{self.device}|"
        return prefix + self.text.replace("\n", "\n    ")


def parseMessage(payload: bytes, peer: tuple, transport: str, encoding: str) -> LogMessage:
    raw = plainText(payload.decode(encoding, errors="replace").rstrip("\r\n"))
    text, level, device = raw, "INFO", ""
    priority = PRIORITY.match(raw)
    if priority and int(priority[1]) <= 191:
        level = SEVERITIES[int(priority[1]) % 8]
    else:
        match = LEVEL.search(raw)
        if match:
            level = match[1].upper()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            obj = None
            
        if isinstance(obj, dict):
            candidate = obj.get("level", obj.get("severity", level))
            
            device = obj.get("dvc", obj.get("device", ""))
            device = " ".join(plainText(device).splitlines()).replace("\t", " ") if isinstance(device, str) else ""
            
            if isinstance(candidate, str) and LEVEL.fullmatch(candidate):
                level = candidate.upper()
            message = obj.get("message", obj.get("msg"))
            
            if isinstance(message, str):
                text = plainText(message)
                extra = {key: value for key, value in obj.items() if key not in ("message", "msg", "level", "severity", "dvc", "device")}
                if extra:
                    text += " " + json.dumps(extra, ensure_ascii=False)
    
    level = {"WARN": "WARNING", "FATAL": "CRITICAL"}.get(level, level)
    
    return LogMessage(datetime.now().astimezone().isoformat(timespec="milliseconds"), peer[0], peer[1], transport, level, device, text, raw)
