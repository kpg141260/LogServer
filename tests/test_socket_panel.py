import os
import socket
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.runtime import ApplicationSetup
from core.startup import StartupPaths, setupConfiguration
from test_log_controls import APP, visible_text
from test_log_file import pump_until
from ui.main_window import MainWindow


class SocketPanelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = StartupPaths(self.root / "data", self.root / "config", self.root / "state.json")
        self.manager = setupConfiguration(self.paths)
        self.manager.config.general.auto_start = False
        self.manager.config.general.persist = False
        self.manager.config.general.max_display_lines = 100
        self.manager.config.socket.protocol = "both"
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.manager.config.socket.port = reserve.getsockname()[1]
        self.window = MainWindow(ApplicationSetup(self.manager, self.paths))
        self.window.show()
        APP.processEvents()
        self.connections = []

    def tearDown(self):
        for client in self.connections:
            client.close()
        self.window.close()
        pump_until(lambda: self.window.server is None and self.window.file_loader is None
                   and not self.window.isVisible())
        self.temp.cleanup()

    def start(self):
        self.window.startServer()
        pump_until(lambda: self.window._server_state == "Listening")

    def connect(self):
        client = socket.create_connection(self.window.server.address[:2], timeout=2)
        self.connections.append(client)
        key = ("tcp", *client.getsockname()[:2])
        pump_until(lambda: key in self.window.client_logs)
        return client, key, self.window.client_logs[key]

    def tab_title(self, view):
        return self.window.socket_tabs.tabText(self.window.socket_tabs.indexOf(view))

    def test_clients_have_separate_tabs_and_keep_logs_after_disconnect_and_stop(self):
        window = self.window
        self.start()
        first, first_key, first_view = self.connect()
        second, second_key, second_view = self.connect()
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(window.socket_tabs.count(), 3)
        self.assertEqual(first_view.toPlainText(), "")
        self.assertIn("Connected", self.tab_title(first_view))
        first.sendall(b"INFO first client\npartial on disconnect")
        second.sendall(b"ERROR second client\n")
        pump_until(lambda: window.received == 2)
        first.close()
        pump_until(lambda: first_key not in window.connected_clients and window.received == 3)
        self.assertIn("Disconnected", self.tab_title(first_view))
        self.assertIn("partial on disconnect", first_view.toPlainText())
        self.assertIn("first client", first_view.toPlainText())
        self.assertNotIn("second client", first_view.toPlainText())
        self.assertIn("second client", second_view.toPlainText())
        self.assertNotIn("first client", second_view.toPlainText())
        window.stopServer()
        pump_until(lambda: window.server is None)
        self.assertIn("Disconnected", self.tab_title(second_view))
        self.assertEqual(window.socket_tabs.count(), 3)
        window.socket_tabs.setCurrentWidget(first_view)
        window.socket_tabs.tabCloseRequested.emit(window.socket_tabs.indexOf(first_view))
        self.assertNotIn(first_key, window.client_logs)
        self.assertIsNot(window.active_log, first_view)
        self.assertIn("partial on disconnect", window.log.toPlainText())
        self.assertIn("second client", window.log.toPlainText())
        stored = (Path(self.manager.config.logging.directory) / "devices.log").read_text()
        self.assertIn("partial on disconnect", stored)
        self.assertIn("second client", stored)
        window.socket_tabs.tabCloseRequested.emit(0)
        self.assertEqual(window.socket_tabs.count(), 2)

    def test_each_client_keeps_independent_controls_and_receives_configuration_changes(self):
        window = self.window
        self.start()
        first, _, first_view = self.connect()
        second, _, second_view = self.connect()
        first.sendall(b"INFO Alpha first\nERROR failed first\n")
        second.sendall(b"WARNING beta second\n")
        pump_until(lambda: window.received == 3)
        window.socket_tabs.setCurrentWidget(first_view)
        self.assertIs(window.active_log, first_view)
        window.search.setText("Alpha")
        window.match_case.setChecked(True)
        window.line_numbers_action.setChecked(True)
        window._setFilter("severity", "ERROR")
        window.socket_tabs.setCurrentWidget(second_view)
        self.assertIs(window.active_log, second_view)
        self.assertEqual(window.search.text(), "")
        self.assertFalse(window.line_numbers_action.isChecked())
        window.search.setText("beta")
        window.socket_tabs.setCurrentWidget(first_view)
        self.assertEqual(window.search.text(), "Alpha")
        self.assertTrue(window.match_case.isChecked())
        self.assertTrue(window.line_numbers_action.isChecked())
        self.assertEqual(first_view.filters["severity"], "ERROR")
        self.assertNotIn("Alpha", visible_text(first_view))
        path = self.root / "offline.log"
        path.write_text("INFO offline", encoding="utf-8")
        window.loadLogFile(path)
        pump_until(lambda: window.file_loader is None)
        window.search.setText("offline")
        QTest.mouseClick(first_view.viewport(), Qt.MouseButton.LeftButton)
        self.assertIs(window.active_log, first_view)
        self.assertEqual(window.search.text(), "Alpha")
        self.assertEqual(window.file_log.search_text, "offline")
        window.clear_action.trigger()
        self.assertEqual(first_view.toPlainText(), "")
        self.assertIn("beta", second_view.toPlainText())
        self.assertIn("Alpha", window.log.toPlainText())
        self.manager.config.general.max_display_lines = 2
        window._configurationChanged(self.manager.config)
        first.sendall(b"INFO old\nINFO middle\nINFO newest\n")
        pump_until(lambda: window.received == 6)
        self.assertEqual(first_view.blockCount(), 2)
        self.assertNotIn("old", first_view.toPlainText())
        self.assertIn("newest", first_view.toPlainText())
        self.assertEqual(second_view.document().maximumBlockCount(), 2)

    def test_udp_senders_get_tabs_distinct_from_tcp_on_the_same_address_and_port(self):
        window = self.window
        self.start()
        tcp, tcp_key, tcp_view = self.connect()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connections.append(udp)
        udp.bind(tcp.getsockname()[:2])
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connections.append(other)
        other.bind(("127.0.0.1", 0))
        udp.sendto(b"WARNING datagram one", window.server.address[:2])
        other.sendto(b"INFO datagram two", window.server.address[:2])
        tcp.sendall(b"ERROR stream\n")
        pump_until(lambda: window.received == 3)
        first_view = window.client_logs[("udp", *udp.getsockname())]
        other_view = window.client_logs[("udp", *other.getsockname())]
        self.assertEqual(window.socket_tabs.count(), 4)
        self.assertIn("datagram one", first_view.toPlainText())
        self.assertNotIn("datagram two", first_view.toPlainText())
        self.assertIn("datagram two", other_view.toPlainText())
        self.assertIn("stream", tcp_view.toPlainText())
        self.assertNotIn("datagram", tcp_view.toPlainText())
        self.assertEqual(window.connected_clients, {tcp_key})
        window.stopServer()
        pump_until(lambda: window.server is None)
        self.assertIn("datagram one", first_view.toPlainText())

    def test_view_menu_hides_panels_without_losing_logs_or_stopping_reception(self):
        window = self.window
        view_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "&View")
        self.assertIn(window.show_socket_action, view_menu.actions())
        self.assertIn(window.show_file_action, view_menu.actions())
        self.assertTrue(window.show_socket_action.isChecked())
        self.assertFalse(window.show_file_action.isChecked())
        window.show_file_action.trigger()
        self.assertTrue(window.file_pane.isVisible())
        window.show_file_action.trigger()
        self.assertTrue(window.file_pane.isHidden())
        self.start()
        client, _, client_view = self.connect()
        path = self.root / "file.log"
        path.write_text("INFO preserved file", encoding="utf-8")
        window.loadLogFile(path)
        pump_until(lambda: window.file_loader is None)
        self.assertTrue(window.show_file_action.isChecked())
        window.search.setText("preserved")
        window.socket_tabs.setCurrentWidget(client_view)
        window.search.setText("hidden")
        window.show_socket_action.trigger()
        self.assertTrue(window.socket_pane.isHidden())
        self.assertIs(window.active_log, window.file_log)
        self.assertEqual(window.search.text(), "preserved")
        window.show_file_action.trigger()
        self.assertTrue(window.file_pane.isHidden())
        self.assertFalse(window.search.isEnabled())
        self.assertFalse(window.clear_action.isEnabled())
        client.sendall(b"INFO hidden delivery\n")
        pump_until(lambda: window.received == 1)
        self.assertIn("hidden delivery", client_view.toPlainText())
        window.show_socket_action.trigger()
        self.assertIs(window.active_log, client_view)
        self.assertEqual(window.search.text(), "hidden")
        self.assertTrue(window.search.isEnabled())
        window.show_file_action.trigger()
        self.assertEqual(window.file_log.toPlainText(), "INFO preserved file")
        window.show_socket_action.trigger()
        window.close_log_action.trigger()
        self.assertFalse(window.show_file_action.isChecked())
        self.assertTrue(window.socket_pane.isHidden())
        self.assertFalse(window.search.isEnabled())
        self.assertEqual(window.file_log.toPlainText(), "")
        window.show_socket_action.trigger()
        self.assertIs(window.active_log, client_view)

    def test_hiding_file_panel_preserves_both_merge_previews_and_controls(self):
        window = self.window
        first = self.root / "first.log"
        second = self.root / "second.log"
        first.write_text("2026-11-19T00:00:00Z INFO first", encoding="utf-8")
        second.write_text("2026-11-19T00:00:01Z ERROR second", encoding="utf-8")
        window.loadMergeFiles((first, second))
        pump_until(lambda: window.file_loader is None)
        window.pane_selector.setCurrentIndex(2)
        window.search.setText("second")
        window.show_file_action.trigger()
        self.assertIs(window.active_log, window.log)
        self.assertFalse(window.pane_selector.model().item(2).isEnabled())
        self.assertTrue(window.merge_button.isEnabled())
        window.show_file_action.trigger()
        window.pane_selector.setCurrentIndex(2)
        self.assertIs(window.active_log, window.second_file_log)
        self.assertEqual(window.search.text(), "second")
        self.assertIn("INFO first", window.file_log.toPlainText())
        self.assertIn("ERROR second", window.second_file_log.toPlainText())
        self.assertEqual(window.merge_paths, (str(first), str(second)))


if __name__ == "__main__":
    unittest.main()
