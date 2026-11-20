"""Formatting for received-log files and the Preferences preview."""
import re
from datetime import datetime

from core.messages import LEVEL, LogMessage


TIMESTAMP = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
DEVICE_HEADER = re.compile(
    rf"{TIMESTAMP}[ \t]+"
    rf"(?:\[[ \t]*{LEVEL.pattern}[ \t]*\]|{LEVEL.pattern})[ \t]*(?:[-:][ \t]*)?", re.IGNORECASE)
ORIGIN_TIMESTAMP = re.compile(rf"^(?P<stamp>{TIMESTAMP})(?=[ \t]|$)[ \t]*")
ORIGIN_SEVERITY = re.compile(
    rf"^(?:\[[ \t]*(?P<bracket>{LEVEL.pattern})[ \t]*\]|(?P<bare>{LEVEL.pattern}))"
    r"(?=[ \t]|[-:]|$)[ \t]*(?:[-:][ \t]*)?", re.IGNORECASE)
FORMATTED_HEADER = re.compile(
    rf"^(?:{TIMESTAMP} \[(?:LTS|DTS)\] )*"
    r"(?:\[(?P<ip>[^\]\n]*[.:][^\]\n]*)\] )?"
    r"(?:\[(?P<protocol>TCP|UDP)\] )?"
    rf"\*\*(?P<severity>{LEVEL.pattern})\*\* - ")


def formatFileMessage(message: LogMessage, settings) -> str:
    text, stamp, level = message.text, "", message.level
    match = ORIGIN_TIMESTAMP.match(text)
    if match:
        try:
            datetime.fromisoformat(match["stamp"].replace(",", "."))
        except ValueError:
            pass
        else:
            stamp, text = match["stamp"], text[match.end():]
    match = ORIGIN_SEVERITY.match(text)
    if match:
        level = (match["bracket"] or match["bare"]).upper()
        level = {"WARN": "WARNING", "FATAL": "CRITICAL"}.get(level, level)
        text = text[match.end():]
    parts = []
    if settings.use_local_timestamp:
        parts.append(f"{message.received} [LTS]")
    if settings.use_origin_timestamp and stamp:
        parts.append(f"{stamp} [DTS]")
    if settings.add_device_ip:
        parts.append(f"[{message.client}]")
    if settings.add_device_protocol:
        parts.append(f"[{message.transport.upper()}]")
    parts.append(f"**{level}** -")
    device = f"{message.device} " if message.device else ""
    return " ".join(parts) + " " + device + text.replace("\n", "\n    ")
