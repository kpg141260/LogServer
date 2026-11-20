import copy
import itertools
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog

from common.logger import MessageFileWriter
from core.config import ConfigManager, LoggingSettings, validate
from core.log_format import formatFileMessage
from core.log_merge import mergeLogFiles
from core.messages import parseMessage
from ui.log_view import LogView, logFields
from ui.wrappers.preferences_wrapper import PreferencesDialog


APP = QApplication.instance() or QApplication([])
SOURCE = "2026-10-29 05:35:29 [ critical] Core 1 UART stack allocation failed; reset required [44192 bytes free]"
BODY = "Core 1 UART stack allocation failed; reset required [44192 bytes free]"
LOCAL = "2026-10-29T11:35:29.139+08:00"
ORIGIN = "2026-10-29 05:35:29"


class LogFormatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = ConfigManager(self.root / "logserver.toml")
        self.manager.config.logging.directory = str(self.root)
        self.message = replace(parseMessage(SOURCE.encode(), ("10.10.0.146", 1234), "tcp", "utf-8"), received=LOCAL)

    def tearDown(self):
        for widget in APP.topLevelWidgets():
            widget.close()
        APP.processEvents()
        self.temp.cleanup()

    def test_all_option_combinations_and_exact_examples(self):
        for origin, local, ip, protocol in itertools.product((False, True), repeat=4):
            settings = LoggingSettings(use_origin_timestamp=origin, use_local_timestamp=local,
                                       add_device_ip=ip, add_device_protocol=protocol)
            prefix = ((LOCAL + " [LTS] " if local else "") +
                      (ORIGIN + " [DTS] " if origin else "") +
                      ("[10.10.0.146] " if ip else "") + ("[TCP] " if protocol else ""))
            with self.subTest(origin=origin, local=local, ip=ip, protocol=protocol):
                self.assertEqual(formatFileMessage(self.message, settings), prefix + "**CRITICAL** - " + BODY)
        self.assertEqual(formatFileMessage(self.message, LoggingSettings()),
                         f"{ORIGIN} [DTS] [10.10.0.146] **CRITICAL** - {BODY}")
        self.assertEqual(self.message.text, SOURCE)
        self.assertEqual(self.message.raw, SOURCE)

    def test_peer_prefix_variants_plain_json_syslog_and_multiline(self):
        cases = (
            ("2026-10-29T05:35:29.123Z WARN - hot\nmore", "2026-10-29T05:35:29.123Z [DTS] [10.10.0.146] **WARNING** - hot\n    more"),
            ("2026-10-29 05:35:29,12 [ fatal ]: failed", "2026-10-29 05:35:29,12 [DTS] [10.10.0.146] **CRITICAL** - failed"),
            ("2026-10-29 05:35:29 ready", "2026-10-29 05:35:29 [DTS] [10.10.0.146] **INFO** - ready"),
            ("ERROR failed", "[10.10.0.146] **ERROR** - failed"),
            ("ordinary message", "[10.10.0.146] **INFO** - ordinary message"),
            ("payload mentions 2026-10-29 05:35:29 [ info ] ready", "[10.10.0.146] **INFO** - payload mentions 2026-10-29 05:35:29 [ info ] ready"),
            ("2026-99-29 05:35:29 [ info ] invalid", "[10.10.0.146] **INFO** - 2026-99-29 05:35:29 [ info ] invalid"),
            ('{"level":"error","message":"failed","device":"pico","n":2}', '[10.10.0.146] **ERROR** - pico failed {"n": 2}'),
            ("<11>Oct 29 05:35:29 pico failed", "[10.10.0.146] **ERROR** - <11>Oct 29 05:35:29 pico failed"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                message = parseMessage(source.encode(), ("10.10.0.146", 0), "tcp", "utf-8")
                self.assertEqual(formatFileMessage(message, LoggingSettings()), expected)

    def test_writer_uses_format_for_both_modes_and_preserves_existing_data(self):
        for mode in ("summary", "client"):
            directory = self.root / mode
            settings = LoggingSettings(directory=str(directory), mode=mode, use_local_timestamp=True)
            writer = MessageFileWriter(settings)
            writer.write(self.message)
            writer.close()
            path, = directory.glob("*.log")
            expected = f"{LOCAL} [LTS] {ORIGIN} [DTS] [10.10.0.146] **CRITICAL** - {BODY}\n"
            self.assertEqual(path.read_text(), expected)
            settings.use_origin_timestamp = False
            settings.add_device_ip = False
            writer = MessageFileWriter(settings)
            writer.write(self.message)
            writer.close()
            self.assertEqual(path.read_text(), expected + f"{LOCAL} [LTS] **CRITICAL** - {BODY}\n")

    def test_config_defaults_roundtrip_validation_and_cancel(self):
        original = copy.deepcopy(self.manager.config.to_mapping())
        dialog = PreferencesDialog(self.manager)
        self.assertIn("Log Format", [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())])
        self.assertTrue(dialog.use_origin_timestamp.isChecked())
        self.assertFalse(dialog.use_local_timestamp.isChecked())
        self.assertTrue(dialog.add_device_ip.isChecked())
        self.assertFalse(dialog.add_device_protocol.isChecked())
        dialog.use_origin_timestamp.setChecked(False)
        dialog.use_local_timestamp.setChecked(True)
        dialog.add_device_ip.setChecked(False)
        dialog.add_device_protocol.setChecked(True)
        dialog.reject()
        self.assertEqual(self.manager.config.to_mapping(), original)
        self.assertFalse(self.manager.config_path.exists())
        dialog = PreferencesDialog(self.manager)
        dialog.use_origin_timestamp.setChecked(False)
        dialog.use_local_timestamp.setChecked(True)
        dialog.add_device_ip.setChecked(False)
        dialog.add_device_protocol.setChecked(True)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        loaded = ConfigManager(self.manager.config_path).config
        self.assertEqual(loaded.to_mapping(), self.manager.config.to_mapping())
        self.assertFalse(loaded.logging.use_origin_timestamp)
        self.assertTrue(loaded.logging.use_local_timestamp)
        self.assertFalse(loaded.logging.add_device_ip)
        self.assertTrue(loaded.logging.add_device_protocol)
        for name in ("use_origin_timestamp", "use_local_timestamp", "add_device_ip", "add_device_protocol"):
            invalid = copy.deepcopy(loaded)
            setattr(invalid.logging, name, 1)
            with self.assertRaisesRegex(ValueError, name):
                validate(invalid)
        self.manager.config_path.write_text('[logging]\ndirectory = "' + self.root.as_posix() + '"\n')
        legacy = ConfigManager(self.manager.config_path).config.logging
        self.assertTrue(legacy.use_origin_timestamp)
        self.assertFalse(legacy.use_local_timestamp)
        self.assertTrue(legacy.add_device_ip)
        self.assertFalse(legacy.add_device_protocol)

    def test_preview_updates_for_examples_and_matches_saved_output(self):
        dialog = PreferencesDialog(self.manager)
        self.assertEqual(dialog.format_preview.toPlainText(), f"{ORIGIN} [DTS] [10.10.0.146] **CRITICAL** - {BODY}")
        examples = (
            ((True, True, True, True), f"{LOCAL} [LTS] {ORIGIN} [DTS] [10.10.0.146] [TCP] **CRITICAL** - {BODY}"),
            ((True, False, True, True), f"{ORIGIN} [DTS] [10.10.0.146] [TCP] **CRITICAL** - {BODY}"),
            ((False, True, True, False), f"{LOCAL} [LTS] [10.10.0.146] **CRITICAL** - {BODY}"),
            ((False, False, False, False), f"**CRITICAL** - {BODY}"),
        )
        for (origin, local, ip, protocol), expected in examples:
            for name, value in (("use_origin_timestamp", origin), ("use_local_timestamp", local),
                                ("add_device_ip", ip), ("add_device_protocol", protocol)):
                getattr(dialog, name).setChecked(value)
            self.assertEqual(dialog.format_preview.toPlainText(), expected)
            self.assertEqual(formatFileMessage(self.message, dialog.draft.logging), expected)

    def test_file_metadata_coloring_filters_and_timestamp_merge(self):
        for origin, local, ip, protocol in itertools.product((False, True), repeat=4):
            settings = LoggingSettings(use_origin_timestamp=origin, use_local_timestamp=local,
                                       add_device_ip=ip, add_device_protocol=protocol)
            text = formatFileMessage(self.message, settings)
            fields = logFields(text)
            self.assertEqual(fields, {"ip": "10.10.0.146" if ip else "", "protocol": "TCP" if protocol else "",
                                      "severity": "CRITICAL", "device": ""})
            view = LogView()
            view.setLines([(text, fields)])
            view.highlighter.rehighlight()
            start = text.index("CRITICAL")
            self.assertTrue(any(run.start <= start < run.start + run.length and run.format.fontWeight() == QFont.Weight.Bold
                                for run in view.document().firstBlock().layout().formats()))
            view.setFilters(severity="INFO")
            self.assertFalse(view.document().firstBlock().isVisible())
            view.setFilters(severity="CRITICAL")
            self.assertTrue(view.document().firstBlock().isVisible())
        first, second, output = (self.root / name for name in ("a.log", "b.log", "merged.log"))
        message = replace(self.message, text=SOURCE.replace(ORIGIN, ORIGIN + "+08:00"))
        first.write_text(formatFileMessage(message, LoggingSettings(use_local_timestamp=True)) + "\n")
        second.write_text(formatFileMessage(message, LoggingSettings()) + "\n")
        self.assertEqual(mergeLogFiles((first, second), output), 2)
        self.assertEqual(output.read_text(), second.read_text() + first.read_text())
        original = output.read_bytes()
        with first.open("a") as target:
            target.write(formatFileMessage(self.message, LoggingSettings(use_origin_timestamp=False)) + "\n")
        with self.assertRaisesRegex(ValueError, "timestamp"):
            mergeLogFiles((first, second), output)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
