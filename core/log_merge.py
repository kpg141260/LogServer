import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QIODevice, QSaveFile

from core.log_format import FORMATTED_HEADER


STAMP = re.compile(rb"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)?)(?=\s|\]|$)")
DATE_PREFIX = re.compile(rb"^\[?\d{4}-\d{2}-\d{2}")


def mergeLogFiles(paths, destination, interrupted=lambda: False):
    paths = tuple(Path(path).resolve() for path in paths)
    destination = Path(destination)
    if len(paths) != 2 or paths[0] == paths[1] or paths[0].samefile(paths[1]):
        raise ValueError("Choose two different source log files.")

    def check_destination():
        for path in paths:
            if destination.resolve() == path or destination.exists() and destination.samefile(path):
                raise ValueError("The merged output must be different from both source files.")

    def check_interruption():
        if interrupted():
            raise InterruptedError("Merge cancelled.")

    check_destination()
    output = QSaveFile(str(destination))
    try:
        with TemporaryDirectory(prefix="logserver-merge-") as directory:
            with closing(sqlite3.connect(str(Path(directory) / "records.sqlite"))) as records:
                records.execute("PRAGMA temp_store=FILE")
                records.set_progress_handler(lambda: int(interrupted()), 1000)
                records.execute("CREATE TABLE lines (stamp TEXT NOT NULL, payload BLOB NOT NULL)")
                count = 0
                for path in paths:
                    check_interruption()
                    if not path.is_file():
                        raise OSError(f"Choose an existing regular log file: {path}")
                    stamp = None
                    with path.open("rb") as source:
                        for number, raw in enumerate(source, 1):
                            check_interruption()
                            if number == 1:
                                raw = raw.removeprefix(b"\xef\xbb\xbf")
                                if not raw:
                                    continue
                            match = STAMP.match(raw)
                            if match:
                                try:
                                    value = datetime.fromisoformat(match[1].decode("ascii").replace(",", "."))
                                    stamp = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
                                except (ValueError, OverflowError, OSError) as exc:
                                    raise ValueError(f"{path}, line {number}: invalid timestamp.") from exc
                                count += 1
                            elif (DATE_PREFIX.match(raw) or stamp is None or
                                  FORMATTED_HEADER.match(raw.decode("utf-8", errors="replace"))):
                                raise ValueError(f"{path}, line {number}: missing or unsupported timestamp.")
                            records.execute("INSERT INTO lines VALUES (?, ?)", (stamp, raw))
                records.commit()
                check_interruption()
                if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                    raise OSError(output.errorString())
                previous = b"\n"
                # Row order keeps continuation lines and equal-time entries in source order.
                for (raw,) in records.execute("SELECT payload FROM lines ORDER BY stamp, rowid"):
                    check_interruption()
                    if not previous.endswith(b"\n"):
                        if output.write(b"\n") != 1:
                            raise OSError(output.errorString())
                    if output.write(raw) != len(raw):
                        raise OSError(output.errorString())
                    previous = raw
                check_interruption()
                check_destination()
                if not output.commit():
                    raise OSError(output.errorString())
                return count
    except sqlite3.Error as exc:
        check_interruption()
        raise OSError(f"Cannot sort log files: {exc}") from exc
    finally:
        if output.isOpen():
            output.cancelWriting()
