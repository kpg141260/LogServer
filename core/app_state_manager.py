__VERSION__ = "0.1.0.3"
# ruff: noqa: RUF100, UP025, UP004, I001, F401

import base64
import binascii
import json
import logging
from functools import wraps
from pathlib import Path
from threading import RLock

from PySide6.QtCore import QByteArray, QDataStream, QIODevice, QRect, QSaveFile, Qt
from PySide6.QtGui import QGuiApplication


_STATE_LOCK = RLock()


def _locked(method):
    @wraps(method)
    def call(*args, **kwargs):
        with _STATE_LOCK:
            return method(*args, **kwargs)
    return call


class AppStateManager:
    def __init__(self, statefile: str, logger=None, level=logging.WARNING):
        self._path = Path(statefile).expanduser() if statefile else None
        if logger is None:
            self._logger = logging.getLogger(__name__)
            self._logger.setLevel(level)
            if not self._logger.handlers:  # avoid duplicate handlers on re-import
                handler = logging.StreamHandler()  # defaults to sys.stderr
                handler.setLevel(level)
                formatter = logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                )
                handler.setFormatter(formatter)
                self._logger.addHandler(handler)
        else:
            self._logger = logger

    def _read_state(self) -> dict:
        state = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("Unsupported window state format")
        return state

    def setupComplete(self) -> bool:
        if self._path is None:
            return False
        try:
            return self._read_state().get("setup") == {"version": 1, "complete": True}
        except FileNotFoundError:
            return False
        except (OSError, ValueError, UnicodeError) as exc:
            self._logger.warning("Cannot read setup state from %s: %s", self._path, exc)
            return False

    @_locked
    def saveSetup(self, complete: bool) -> None:
        """Persist setup status; failures must reach the startup dialog."""
        try:
            state = self._read_state()
        except (FileNotFoundError, ValueError, UnicodeError):
            state = {"version": 1}
        state["setup"] = {"version": 1, "complete": complete}
        self._write_state(state)

    def _write_state(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(state).encode("utf-8")
        output = QSaveFile(str(self._path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())

    @_locked
    def saveWindow(self, window, key: str = "main") -> bool:
        if self._path is None:
            return False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            screen = window.screen()
            offset = (window.normalGeometry().topLeft()
                      if window.isMaximized() or window.isFullScreen() else window.pos())
            if screen is not None:
                offset -= screen.geometry().topLeft()
            window_state = {
                "version": 1,
                "geometry": base64.b64encode(bytes(window.saveGeometry())).decode("ascii"),
                "normal_size": [window.normalGeometry().width(), window.normalGeometry().height()]
                if window.isMaximized() or window.isFullScreen() else [window.width(), window.height()],
                "screen": screen.name() if screen else "",
                "screen_position": [offset.x(), offset.y()],
            }
            if key == "main" and hasattr(window, "_save_column_visibility"):
                window_state["column_visibility"] = window._save_column_visibility()
            if key == "main" and hasattr(window, "_save_column_layout"):
                window_state["column_layout"] = window._save_column_layout()
            try:
                state = self._read_state()
            except FileNotFoundError:
                state = {"version": 1}
            except ValueError as exc:
                self._logger.warning("Replacing invalid window state in %s: %s", self._path, exc)
                state = {"version": 1}
            if key == "main":
                state.update(window_state)
            else:
                windows = state.setdefault("windows", {})
                if not isinstance(windows, dict):
                    raise ValueError("Invalid plot window states")
                windows[key] = window_state
            self._logger.debug(f'{__class__} saving current state for window {key}')
            self._write_state(state)
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            self._logger.warning("Cannot save window state to %s: %s", self._path, exc)
            return False

    def restoreWindow(self, window, key: str = "main") -> bool:
        if self._path is None:
            return False
        try:
            self._logger.debug(f'{__class__} attempting to restore state for window {key}')
            state = self._read_state()
            if key != "main":
                windows = state.get("windows", {})
                if not isinstance(windows, dict):
                    raise ValueError("Invalid plot window states")
                state = windows.get(key)
                if state is None:
                    return False
            elif "geometry" not in state and ("windows" in state or "setup" in state):
                return False
            if not isinstance(state, dict) or state.get("version") != 1:
                raise ValueError("Unsupported window state format")
            restored = True
            try:
                self._restore_geometry(window, state)
            except (ValueError, KeyError, TypeError, RuntimeError, binascii.Error) as exc:
                self._logger.warning("Cannot restore window state from %s: %s", self._path, exc)
                restored = False
            if key == "main":
                for field, method in (("column_layout", "_restore_column_layout"),
                                      ("column_visibility", "_restore_column_visibility")):
                    if field in state and hasattr(window, method):
                        try:
                            getattr(window, method)(state[field])
                        except (ValueError, TypeError, RuntimeError, binascii.Error) as exc:
                            self._logger.warning("Cannot restore window state from %s (%s): %s", self._path, field, exc)
                            restored = False
            return restored
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, UnicodeError, binascii.Error) as exc:
            self._logger.warning("Cannot restore window state from %s: %s", self._path, exc)
            return False

    def _restore_geometry(self, window, state: dict) -> None:
        position = state.get("screen_position")
        if position is not None and (
            not isinstance(position, list) or len(position) != 2
            or any(type(value) is not int or not -(2**31) <= value < 2**31 for value in position)
        ):
            raise ValueError("Invalid screen position")
        size = state.get("normal_size")
        if size is not None and (
            not isinstance(size, list) or len(size) != 2
            or any(type(value) is not int or not 0 < value < 2**31 for value in size)
        ):
            raise ValueError("Invalid normal window size")
        geometry = base64.b64decode(state["geometry"], validate=True)
        if not geometry or not window.restoreGeometry(QByteArray(geometry)):
            if size is None:
                self._recover_legacy_geometry(window, geometry)
            else:
                self._logger.warning("Qt rejected saved geometry in %s; restoring explicit window size", self._path)
                window.resize(*size)
        screens = QGuiApplication.screens()
        screen = next((item for item in screens if item.name() == state.get("screen")), None)
        if screen is not None:
            window.setScreen(screen)
            if position is not None and not window.isMaximized() and not window.isFullScreen():
                window.move(screen.geometry().x() + position[0], screen.geometry().y() + position[1])
        else:
            screen = window.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            frame = window.frameGeometry()
            if not available.intersects(frame):
                window.move(available.topLeft())


    def _recover_legacy_geometry(self, window, geometry: bytes) -> None:
        try:
            stream = QDataStream(QByteArray(geometry))
            stream.setVersion(QDataStream.Version.Qt_4_0)
            if stream.readUInt32() != 0x1D9D0CB:
                raise ValueError("Invalid Qt geometry signature")
            version, minor = stream.readUInt16(), stream.readUInt16()
            if version not in (1, 2, 3):
                raise ValueError(f"Unsupported Qt geometry version {version}.{minor}")
            frame, normal, current = QRect(), QRect(), QRect()
            stream >> frame >> normal
            stream.readInt32()  # Screen index is handled by the saved screen name.
            maximized, fullscreen = stream.readUInt8(), stream.readUInt8()
            if version >= 2:
                stream.readInt32()  # Saved screen width can cause Qt to reject restoration.
            if version >= 3:
                stream >> current
            rect = current if version >= 3 and not (maximized or fullscreen) else normal
            if stream.status() != QDataStream.Status.Ok or not rect.isValid():
                raise ValueError("Truncated or invalid Qt geometry rectangle")
            window.setGeometry(rect)
            if fullscreen:
                window.setWindowState(Qt.WindowState.WindowFullScreen)
            elif maximized:
                window.setWindowState(Qt.WindowState.WindowMaximized)
            self._logger.warning("Recovered legacy window geometry from %s: x=%s y=%s width=%s height=%s", self._path,
                           rect.x(), rect.y(), rect.width(), rect.height())
        except (ValueError, TypeError, RuntimeError) as exc:
            self._logger.critical("Legacy window geometry recovery failed for %s: %s", self._path, exc)
            raise
