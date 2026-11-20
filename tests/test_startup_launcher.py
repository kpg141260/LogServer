import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSaveFile
from PySide6.QtWidgets import QApplication, QDialog

from core.startup import StartupPaths, installDesktopLauncher, setupReason
from ui.startup_dialog import StartupDialog


APP = QApplication.instance() or QApplication([])


class StartupLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = StartupPaths(self.root / "data", self.root / "config", self.root / "state")
        self.environment = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "desktop data")})
        self.environment.start()

    def tearDown(self):
        for widget in APP.topLevelWidgets():
            widget.close()
        APP.processEvents()
        self.environment.stop()
        self.temp.cleanup()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop permissions and paths")
    def test_source_launcher_preserves_virtualenv_and_escapes_paths(self):
        root = self.root / 'source $cash "quote" `tick` \\ %f 雨'
        (root / "core").mkdir(parents=True)
        (root / "assets").mkdir()
        (root / "assets" / "logserver.svg").touch()
        (root / "log_server.py").touch()
        interpreter = self.root / "virtual environment" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        with patch("core.startup.__file__", str(root / "core" / "startup.py")), \
                patch("core.startup.sys.executable", str(interpreter)), \
                patch("core.startup.sys.frozen", False, create=True):
            path = installDesktopLauncher()
        self.assertEqual(path, self.root / "desktop data" / "applications" / "logserver.desktop")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        text = path.read_text(encoding="utf-8")
        escaped = str(root).replace("\\", "\\\\\\\\").replace('"', '\\\\"')
        escaped = escaped.replace("$", "\\\\$").replace("`", "\\\\`").replace("%", "%%")
        self.assertIn(f'Exec="{interpreter}" "{escaped}/log_server.py"\n', text)
        self.assertIn(f'Icon={str(root).replace(chr(92), chr(92) * 2)}/assets/logserver.svg\n', text)
        self.assertIn("Terminal=false\n", text)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux launcher executable paths")
    def test_unsupported_executable_paths_do_not_create_launcher(self):
        for name in ("python%f", "python=3"):
            executable = self.root / name
            executable.touch()
            with self.subTest(name=name), patch("core.startup.sys.executable", str(executable)):
                with self.assertRaisesRegex(ValueError, "executable path"):
                    installDesktopLauncher()
        self.assertFalse((self.root / "desktop data").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop installation")
    def test_frozen_launcher_uses_executable_and_bundled_icon(self):
        bundle = self.root / "package"
        resources = bundle / "_internal"
        (resources / "assets").mkdir(parents=True)
        (resources / "assets" / "logserver.svg").touch()
        (bundle / "LogServer").touch()
        with patch("core.startup.__file__", str(resources / "core" / "startup.py")), \
                patch("core.startup.sys.executable", str(bundle / "LogServer")), \
                patch("core.startup.sys.frozen", True, create=True):
            text = installDesktopLauncher().read_text()
        self.assertIn(f'Exec="{bundle}/LogServer"\n', text)
        self.assertIn(f"Icon={resources}/assets/logserver.svg\n", text)
        self.assertNotIn("log_server.py", text)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux XDG paths")
    def test_empty_xdg_uses_home_and_relative_xdg_is_rejected(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": ""}), patch("core.startup.Path.home", return_value=self.root):
            path = installDesktopLauncher()
        self.assertEqual(path, self.root / ".local" / "share" / "applications" / "logserver.desktop")
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}):
            with self.assertRaisesRegex(ValueError, "absolute path"):
                installDesktopLauncher()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop installation")
    def test_failed_atomic_save_preserves_existing_launcher(self):
        path = installDesktopLauncher()
        original = path.read_bytes()
        for method, result in (("open", False), ("setPermissions", False), ("write", -1), ("commit", False)):
            with self.subTest(method=method), patch.object(QSaveFile, method, return_value=result):
                with self.assertRaises(OSError):
                    installDesktopLauncher()
            self.assertEqual(path.read_bytes(), original)

    def test_cancel_and_unchecked_setup_do_not_install_launcher(self):
        with patch("core.startup.installDesktopLauncher") as install:
            dialog = StartupDialog(self.paths)
            self.assertFalse(dialog.launcher.isChecked())
            dialog.launcher.setChecked(True)
            dialog.reject()
            self.assertFalse(self.paths.state_file.exists())
            dialog = StartupDialog(self.paths)
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        install.assert_not_called()

    def test_non_linux_hides_option_and_never_installs(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), patch("ui.startup_dialog.sys.platform", platform), \
                    patch("core.startup.installDesktopLauncher") as install:
                dialog = StartupDialog(self.paths)
                self.assertTrue(dialog.launcher.isHidden())
                dialog.launcher.setChecked(True)
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                install.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux setup launcher option")
    def test_checked_setup_installs_launcher_and_reports_failure_with_retry(self):
        dialog = StartupDialog(self.paths)
        self.assertFalse(dialog.launcher.isHidden())
        dialog.launcher.setChecked(True)
        with patch("core.startup.installDesktopLauncher", side_effect=OSError("Launcher permission denied")):
            dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("Launcher permission denied", dialog.error.text())
        self.assertTrue(setupReason(self.paths))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(setupReason(self.paths), "")
        path = self.root / "desktop data" / "applications" / "logserver.desktop"
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
