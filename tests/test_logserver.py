import copy
import json
import os
import queue
import random
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QFontDialog

from common.logger import MessageFileWriter
from core.app_state_manager import AppStateManager
from core.config import ApplicationConfig, ConfigManager, validate
from core.config_loader import VERSION, load_config
from core.messages import parseMessage
from core.runtime import ApplicationSetup
from core.server import LogServer
from core.startup import StartupPaths, completeSetup, ensureStartup, firstLaunch, setupConfiguration, setupReason, startupPaths
from ui.log_view import LogView
from ui.appearance import applyAppearance
from ui.main_window import MainWindow
from ui.startup_dialog import StartupDialog
from ui.wrappers.preferences_wrapper import PreferencesDialog

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for application state")


def free_port(family=socket.AF_INET, host="127.0.0.1"):
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = StartupPaths(self.root / "data", self.root / "config", self.root / "state" / "window.json")
        self.manager = setupConfiguration(self.paths)
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.stop()
            self.server.thread.join(3)
            self.assertFalse(self.server.thread.is_alive(), "Receiver thread did not stop")
        for widget in APP.topLevelWidgets():
            widget.close()
        APP.processEvents()
        self.temp.cleanup()

    def configured(self, protocol="tcp"):
        config = copy.deepcopy(self.manager.config)
        config.socket.port = free_port()
        config.socket.protocol = protocol
        return config

    def start(self, config=None):
        self.server = LogServer(config or self.configured())
        self.server.start()
        self.event("started")
        return self.server

    def event(self, kind, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                found, value = self.server.events.get(timeout=0.05)
            except queue.Empty:
                if self.server.failure and kind != "error":
                    self.fail(self.server.failure)
                continue
            if found == kind:
                return value
        self.fail(f"No {kind} event; receiver failure: {self.server.failure}")

    def test_first_launch_save_and_repeat_launch(self):
        self.assertTrue(firstLaunch(self.paths))
        completeSetup(self.paths, self.manager)
        self.assertEqual(setupReason(self.paths), "")
        self.assertFalse(firstLaunch(self.paths))
        ensureStartup(self.paths)
        loaded = ConfigManager(self.paths.configPath())
        self.assertEqual(loaded.config.to_mapping(), self.manager.config.to_mapping())
        self.assertTrue(Path(loaded.config.logging.directory).is_dir())

    def test_setup_cancel_creates_nothing(self):
        dialog = StartupDialog(self.paths)
        dialog.reject()
        self.assertFalse(self.paths.configPath().exists())
        self.assertFalse(self.paths.state_file.exists())

    def test_setup_dialog_saves_and_validates(self):
        dialog = StartupDialog(self.paths)
        dialog.directory.setText("")
        dialog.accept()
        self.assertIn("Choose a folder", dialog.error.text())
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.directory.setText(str(self.root / "received"))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(firstLaunch(self.paths))

    def test_corrupt_state_requires_setup(self):
        completeSetup(self.paths, self.manager)
        self.paths.state_file.write_text("{broken", encoding="utf-8")
        self.assertTrue(firstLaunch(self.paths))

    def test_invalid_toml_does_not_get_overwritten(self):
        self.paths.config_dir.mkdir()
        self.paths.configPath().write_text("[broken", encoding="utf-8")
        dialog = StartupDialog(self.paths)
        dialog.accept()
        self.assertIn("Cannot load configuration", dialog.error.text())
        self.assertEqual(self.paths.configPath().read_text(), "[broken")

    def test_preferences_cancel_keeps_config(self):
        original = self.manager.config.to_mapping()
        dialog = PreferencesDialog(self.manager)
        dialog.host.setText("0.0.0.0")
        dialog.directory.setText(str(self.root / "changed"))
        dialog.reject()
        self.assertEqual(self.manager.config.to_mapping(), original)
        self.assertFalse(self.paths.configPath().exists())

    def test_preferences_save_roundtrip_all_sections(self):
        dialog = PreferencesDialog(self.manager)
        dialog.host.setText("::1")
        dialog.protocol.setCurrentIndex(dialog.protocol.findData("both"))
        dialog.mode.setCurrentIndex(dialog.mode.findData("client"))
        dialog.directory.setText(str(self.root / "spaces and unicode 雨"))
        dialog.appearance.setCurrentIndex(dialog.appearance.findData("dark"))
        dialog.keywords.setText("ready,temperature,voltage")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        config = ConfigManager(self.paths.configPath()).config
        self.assertEqual(config.socket.host, "::1")
        self.assertEqual(config.socket.protocol, "both")
        self.assertEqual(config.logging.mode, "client")
        self.assertEqual(config.general.appearance, "dark")
        self.assertEqual(config.general.keywords, "ready,temperature,voltage")
        self.assertIn("雨", config.logging.directory)

    def test_invalid_preferences_preserve_disk_and_live_settings(self):
        completeSetup(self.paths, self.manager)
        original = self.paths.configPath().read_bytes()
        dialog = PreferencesDialog(self.manager)
        dialog.host.setText("invalid host")
        dialog.accept()
        self.assertIn("IPv4 or IPv6", dialog.error.text())
        self.assertEqual(self.paths.configPath().read_bytes(), original)
        self.assertEqual(self.manager.config.socket.host, "127.0.0.1")

    def test_failed_atomic_save_preserves_live_settings(self):
        self.paths.config_dir.mkdir(parents=True)
        self.paths.configPath().mkdir()
        original = self.manager.config.to_mapping()
        draft = self.manager.createDraft()
        draft.socket.port = 6000
        with self.assertRaises(OSError):
            self.manager.commit(draft)
        self.assertEqual(self.manager.config.to_mapping(), original)
        self.assertTrue(self.paths.configPath().is_dir())

    def test_validation_rejects_invalid_types_limits_and_paths(self):
        cases = (("socket", "port", True), ("socket", "idle_timeout", float("nan")),
                 ("socket", "port", 65536), ("socket", "max_clients", 257),
                 ("socket", "encoding", "utf-16"), ("logging", "mode", "elsewhere"),
                 ("logging", "summary_file", "../escape.log"),
                 ("logging", "summary_file", "CON.txt"),
                 ("logging", "summary_file", "C:\\escape.log"),
                 ("general", "max_display_lines", 0))
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                config = copy.deepcopy(self.manager.config)
                setattr(getattr(config, section), key, value)
                with self.assertRaises(ValueError):
                    validate(config)

    def test_cli_help_and_version_without_gui_or_setup(self):
        for argument, expected in (("--help", "--protocol"), ("--version", f"LogServer {VERSION}")):
            result = subprocess.run([sys.executable, str(ROOT / "main.py"), argument],
                                    cwd=self.root, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_tcp_fragmented_unicode_multiple_lines_and_eof(self):
        server = self.start()
        payload = "INFO 温度 24°C\nERROR ^sensor^ failed\r\nlast line".encode()
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(payload[:7])
            client.sendall(payload[7:])
        messages = [self.event("message") for _ in range(3)]
        self.assertEqual([m.text for m in messages], ["INFO 温度 24°C", "ERROR ^sensor^ failed", "last line"])
        self.assertEqual(messages[1].level, "ERROR")
        stored = (Path(server.config.logging.directory) / "devices.log").read_text(encoding="utf-8")
        for message in messages:
            self.assertIn(message.text, stored)

    def test_udp_syslog_json_and_invalid_utf8(self):
        server = self.start(self.configured("udp"))
        packets = [b"<11>Oct 28 10:00:00 device sensor failed", b'{"level":"warning","message":"temperature high","device":"pico"}', b"INFO \xff"]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            for packet in packets:
                client.sendto(packet, server.address[:2])
        messages = [self.event("message") for _ in packets]
        self.assertEqual([m.level for m in messages], ["ERROR", "WARNING", "INFO"])
        self.assertIn('"device": "pico"', messages[1].text)
        self.assertIn("\ufffd", messages[2].text)

    def test_both_transports_and_concurrent_clients(self):
        server = self.start(self.configured("both"))
        clients = [socket.create_connection(server.address[:2], timeout=2) for _ in range(3)]
        try:
            for index, client in enumerate(clients):
                client.sendall(f"device {index}\n".encode())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.sendto(b"udp device", server.address[:2])
            messages = [self.event("message") for _ in range(4)]
            self.assertEqual({m.text for m in messages}, {"device 0", "device 1", "device 2", "udp device"})
            self.assertEqual({m.transport for m in messages}, {"tcp", "udp"})
        finally:
            for client in clients:
                client.close()

    def test_oversize_tcp_and_udp_rejected_server_survives(self):
        config = self.configured("both")
        config.socket.max_message_bytes = 256
        server = self.start(config)
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"x" * 300)
            self.assertIn("limit exceeded", self.event("warning"))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.sendto(b"x" * 300, server.address[:2])
            self.assertIn("Oversized UDP", self.event("warning"))
            client.sendto(b"still works", server.address[:2])
        self.assertEqual(self.event("message").text, "still works")
        self.assertTrue(server.thread.is_alive())

    def test_client_limit_and_idle_timeout(self):
        config = self.configured()
        config.socket.max_clients = 1
        config.socket.idle_timeout = 0.3
        server = self.start(config)
        with socket.create_connection(server.address[:2], timeout=2) as first:
            self.assertEqual(self.event("clients"), 1)
            with socket.create_connection(server.address[:2], timeout=2) as second:
                self.assertIn("Client limit", self.event("warning"))
                self.assertEqual(second.recv(1), b"")
            first.sendall(b"partial on idle")
            self.assertIn("Idle client", self.event("warning"))
            self.assertEqual(self.event("message").text, "partial on idle")

    def test_bind_error_reports_without_traceback(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            config = self.configured()
            config.socket.port = occupied.getsockname()[1]
            self.server = LogServer(config)
            self.server.start()
            error = self.event("error")
            self.assertTrue(error)
            self.server.thread.join(3)
            self.assertFalse(self.server.thread.is_alive())

    def test_file_open_failure_stops_receiver(self):
        config = self.configured()
        bad = self.root / "not-a-folder"
        bad.write_text("keep")
        config.logging.directory = str(bad)
        self.server = LogServer(config)
        self.server.start()
        self.assertTrue(self.event("error"))
        self.server.thread.join(3)
        self.assertFalse(self.server.thread.is_alive())
        self.assertEqual(bad.read_text(), "keep")

    def test_write_failure_reports_and_stops_before_displaying_as_saved(self):
        config = self.configured()
        config.logging.rotate = False
        server = self.start(config)
        with patch("logging.FileHandler.emit", side_effect=OSError("disk full")):
            with socket.create_connection(server.address[:2], timeout=2) as client:
                client.sendall(b"ERROR important\n")
                self.assertEqual(self.event("error"), "disk full")
                server.thread.join(3)
        self.assertFalse(server.thread.is_alive())
        self.assertEqual(server.failure, "disk full")
        self.assertEqual((Path(server.config.logging.directory) / "devices.log").read_text(), "")

    def test_cli_overrides_toml_without_changing_saved_settings(self):
        with patch("core.startup.QStandardPaths.writableLocation",
                   side_effect=lambda location: str(self.root / str(location.value))):
            paths = startupPaths()
            manager = setupConfiguration(paths)
            manager.config.socket.port = 6500
            completeSetup(paths, manager)
            setup = load_config(["--host", "0.0.0.0", "--port", "6501", "--protocol", "both"])
            self.assertEqual(setup.config_manager.config.socket.port, 6501)
            self.assertEqual(setup.config_manager.config.socket.host, "0.0.0.0")
            self.assertEqual(setup.config_manager.config.socket.protocol, "both")
            self.assertEqual(ConfigManager(paths.configPath()).config.socket.port, 6500)

    def test_received_file_cannot_overwrite_config(self):
        completeSetup(self.paths, self.manager)
        before = self.paths.configPath().read_bytes()
        draft = self.manager.createDraft()
        draft.logging.directory = str(self.paths.config_dir)
        draft.logging.summary_file = self.paths.configPath().name
        with self.assertRaises(ValueError):
            self.manager.commit(draft)
        self.assertEqual(self.paths.configPath().read_bytes(), before)

    def test_startup_reports_invalid_saved_log_directory(self):
        completeSetup(self.paths, self.manager)
        text = self.paths.configPath().read_text()
        text = text.replace(json.dumps(self.manager.config.logging.directory), '""')
        self.paths.configPath().write_text(text)
        self.assertIn("Choose a folder", setupReason(self.paths))

    def test_hostile_message_corpus_and_seeded_bytes(self):
        seeds = [b"", b"\x00\x1b[31mERROR\x1b[0m", b"<999>unexpected priority", b"\xff\xfe\x80",
                 '{"message":"Unicode 雨 🌡️ \\u202e","extra":"value"}'.encode(),
                 b'{"level":"error","level":"debug","message":"duplicate"}',
                 b'{"message":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"]
        randomizer = random.Random(117)
        seeds.extend(randomizer.randbytes(randomizer.randrange(0, 4096)) for _ in range(150))
        for payload in seeds:
            message = parseMessage(payload, ("192.0.2.1", 1234), "tcp", "utf-8")
            self.assertTrue(message.received)
            self.assertNotIn("\x1b", message.text)
            self.assertNotIn("\x00", message.line)
            self.assertEqual(message.client, "192.0.2.1")

    def test_emoji_section_offsets_and_selection(self):
        view = LogView()
        view.configure(self.manager.config.general)
        text = "INFO 🌡️ ^sensor^ temperature"
        view.appendMessage(text)
        view.highlighter.rehighlight()
        expected = len(text[:text.index("^sensor^")].encode("utf-16-le")) // 2
        formats = view.document().firstBlock().layout().formats()
        self.assertTrue(any(run.start == expected and run.length == len("^sensor^") for run in formats))
        cursor = view.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        view.setTextCursor(cursor)
        view.appendMessage("next")
        self.assertEqual(view.textCursor().selectedText(), text)

    def test_appearance_switches_dark_light_and_system(self):
        settings = copy.deepcopy(self.manager.config.general)
        initial = APP.palette().base().color()
        try:
            settings.appearance = "dark"
            applyAppearance(settings)
            self.assertLess(APP.palette().base().color().lightness(), 128)
            settings.appearance = "light"
            applyAppearance(settings)
            self.assertGreater(APP.palette().base().color().lightness(), 128)
        finally:
            settings.appearance = "system"
            applyAppearance(settings)
        self.assertEqual(APP.palette().base().color(), initial)

    def test_autostart_and_preferences_keep_running_listener_snapshot(self):
        self.manager.config = self.configured()
        window = MainWindow(ApplicationSetup(self.manager, self.paths))
        window.show()
        pump_until(lambda: window._server_state == "Listening")
        port = window.server.address[1]
        draft = self.manager.createDraft()
        draft.socket.port = free_port()
        draft.general.keywords = "ready"
        self.manager.commit(draft)
        self.assertEqual(window.server.address[1], port)
        self.assertIn("Stop and Start", window.endpoint.text())
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"ready\n")
        pump_until(lambda: window.received == 1)
        window.close()
        pump_until(lambda: not window.isVisible())

    def test_stop_with_full_display_queue_and_partial_message(self):
        server = self.start()
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"message\n" * 2200 + b"partial")
            deadline = time.monotonic() + 3
            while not server.events.full() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.events.full())
            server.stop()
            server.thread.join(3)
            self.assertFalse(server.thread.is_alive())
        stored = (Path(server.config.logging.directory) / "devices.log").read_text()
        self.assertIn("message", stored)

    def test_server_restarts_on_same_port(self):
        config = self.configured()
        server = self.start(config)
        server.stop()
        server.thread.join(3)
        server = self.start(config)
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"restarted\n")
        self.assertEqual(self.event("message").text, "restarted")

    def test_stop_flushes_partial_line_to_file_and_display(self):
        server = self.start()
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"complete\npartial")
            self.assertEqual(self.event("message").text, "complete")
            server.stop()
            server.thread.join(3)
            self.assertFalse(server.thread.is_alive())
            self.assertEqual(self.event("message").text, "partial")
        self.assertIn("partial", (Path(server.config.logging.directory) / "devices.log").read_text())

    @unittest.skipUnless(socket.has_ipv6, "IPv6 unavailable on this host")
    def test_ipv6_loopback(self):
        config = self.configured()
        config.socket.host = "::1"
        config.socket.port = free_port(socket.AF_INET6, "::1")
        server = self.start(config)
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"IPv6 device\n")
        self.assertEqual(self.event("message").client, "::1")

    def test_files_append_and_per_client_identity_is_safe(self):
        config = self.configured()
        config.logging.mode = "client"
        message = parseMessage(b'{"client":"../../escape","message":"hello"}', ("127.0.0.1", 40000), "tcp", "utf-8")
        for _ in range(2):
            writer = MessageFileWriter(config.logging)
            writer.write(message)
            writer.close()
        files = list(Path(config.logging.directory).glob("*.log"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("client-"))
        self.assertEqual(files[0].read_text().count("hello"), 2)
        self.assertIn("../../escape", files[0].read_text())

    def test_per_client_files_are_separate_and_handles_bounded(self):
        config = self.configured()
        config.logging.mode = "client"
        writer = MessageFileWriter(config.logging)
        try:
            for number in range(70):
                writer.write(parseMessage(f"client {number}".encode(), (f"192.0.2.{number}", 1000), "tcp", "utf-8"))
            self.assertLessEqual(len(writer.handlers), 64)
        finally:
            writer.close()
        files = list(Path(config.logging.directory).glob("*.log"))
        self.assertEqual(len(files), 70)
        self.assertEqual(sum("client " in path.read_text() for path in files), 70)

    def test_rotation_retains_backups_without_truncating_on_restart(self):
        config = self.configured()
        config.logging.max_bytes = 1024
        config.logging.backup_count = 2
        writer = MessageFileWriter(config.logging)
        try:
            for number in range(15):
                writer.write(parseMessage(f"{number} {'x' * 300}".encode(), ("127.0.0.1", 1), "tcp", "utf-8"))
        finally:
            writer.close()
        folder = Path(config.logging.directory)
        self.assertTrue((folder / "devices.log.1").exists())
        self.assertTrue((folder / "devices.log.2").exists())
        self.assertFalse((folder / "devices.log.3").exists())
        before = (folder / "devices.log").read_bytes()
        writer = MessageFileWriter(config.logging)
        writer.close()
        self.assertEqual((folder / "devices.log").read_bytes(), before)

    def test_file_logging_disabled_still_receives(self):
        config = self.configured()
        config.logging.enabled = False
        server = self.start(config)
        with socket.create_connection(server.address[:2], timeout=2) as client:
            client.sendall(b"display only\n")
        self.assertEqual(self.event("message").text, "display only")
        self.assertFalse(Path(config.logging.directory).exists())

    def test_plain_text_json_syslog_and_control_sanitization(self):
        cases = ((b"[WARN] warning", "WARNING"), (b"<0>device emergency", "EMERGENCY"),
                 (b'{"severity":"debug","msg":"ready","count":3}', "DEBUG"),
                 (b'{"broken":', "INFO"), (b"{\"level\":12}", "INFO"))
        for payload, level in cases:
            with self.subTest(payload=payload):
                self.assertEqual(parseMessage(payload, ("127.0.0.1", 1), "tcp", "utf-8").level, level)
        message = parseMessage(b"\x1b[31mERROR\x1b[0m\x00 <b>keep literal</b>", ("127.0.0.1", 1), "tcp", "utf-8")
        self.assertEqual(message.text, "ERROR <b>keep literal</b>")

    def test_rich_display_literal_html_highlights_sections_and_keywords(self):
        view = LogView()
        settings = self.manager.config.general
        settings.keywords = "temperature"
        view.configure(settings)
        message = parseMessage(b"ERROR <b>temperature</b> ^sensor^ failed", ("127.0.0.1", 1), "tcp", "utf-8")
        view.appendMessage(message.line)
        view.highlighter.rehighlight()
        self.assertEqual(view.toPlainText(), message.line)
        block = view.document().firstBlock()
        colors = {run.format.foreground().color().name() for run in block.layout().formats()}
        self.assertGreaterEqual(len(colors), 2)
        section = message.line.index("^sensor^")
        self.assertTrue(any(run.start == section and run.length == len("^sensor^") and
                            run.format.fontWeight() == QFont.Weight.Bold for run in block.layout().formats()))

    def test_display_limit_and_selection_preserved(self):
        view = LogView()
        settings = self.manager.config.general
        settings.max_display_lines = 100
        view.configure(settings)
        view.appendMessage("first line")
        cursor = view.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        view.setTextCursor(cursor)
        view.appendMessage("second line")
        self.assertEqual(view.textCursor().selectedText(), "first line")
        for _ in range(110):
            view.appendMessage("bounded")
        self.assertEqual(view.document().blockCount(), 100)

    def test_main_window_socket_to_display_and_file_then_shutdown(self):
        self.manager.config = self.configured("both")
        self.manager.config.general.auto_start = False
        completeSetup(self.paths, self.manager)
        window = MainWindow(ApplicationSetup(self.manager, self.paths))
        window.resize(640, 480)
        window.show()
        window.startServer()
        pump_until(lambda: window._server_state == "Listening")
        with socket.create_connection(window.server.address[:2], timeout=2) as client:
            client.sendall(b"WARNING ^pico^ temperature high\n")
        pump_until(lambda: window.received == 1)
        self.assertIn("temperature high", window.log.toPlainText())
        self.assertIn("temperature high", (Path(self.manager.config.logging.directory) / "devices.log").read_text())
        thread = window.server.thread
        window.close()
        pump_until(lambda: not window.isVisible())
        self.assertFalse(thread.is_alive())
        restored = MainWindow(ApplicationSetup(self.manager, self.paths))
        self.assertEqual(restored.size(), window.size())
        restored.close()

    def test_main_window_bind_failure_is_visible(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            self.manager.config = self.configured()
            self.manager.config.socket.port = occupied.getsockname()[1]
            self.manager.config.general.auto_start = False
            window = MainWindow(ApplicationSetup(self.manager, self.paths))
            window.show()
            window.startServer()
            pump_until(lambda: window.server is None)
            self.assertTrue(window.error.text())
            self.assertFalse(window.error.isHidden())
            self.assertTrue(window.start_action.isEnabled())
            window.close()


if __name__ == "__main__":
    unittest.main()
