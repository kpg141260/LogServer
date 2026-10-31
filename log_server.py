__VERSION__ = "0.1.0.6"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
import sys
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setApplicationName("LogServer")
    QCoreApplication.setOrganizationName("LogServer")
    if argv[:1] == ["--setup"]:
        if len(argv) != 4:
            raise SystemExit("Usage: log_server.py --setup DATA_DIR CONFIG_DIR STATE_FILE")
        from PySide6.QtWidgets import QApplication, QDialog

        from core.startup import StartupPaths
        from ui.startup_dialog import StartupDialog
        app = QApplication([sys.argv[0]])
        dialog = StartupDialog(StartupPaths(*(Path(value) for value in argv[1:])))
        return 0 if dialog.exec() == QDialog.DialogCode.Accepted else 2

    from core.config_loader import load_config
    setup = load_config(argv)
    from PySide6.QtWidgets import QApplication, QMessageBox

    from common.logger import ErrorLogger
    from ui.main_window import MainWindow
    app = QApplication([sys.argv[0]])
    try:
        diagnostic = ErrorLogger(setup.config_manager.config, setup.paths.data_dir)
    except OSError as exc:
        QMessageBox.critical(None, "Cannot open application log", str(exc))
        return 1

    def unhandled(kind, value, traceback):
        diagnostic.logger.critical("Unexpected application failure", exc_info=(kind, value, traceback))
        sys.__excepthook__(kind, value, traceback)
        QMessageBox.critical(None, "Unexpected application failure", "See application.log for the traceback.")
        app.exit(1)

    previous_hook = sys.excepthook
    sys.excepthook = unhandled
    try:
        window = MainWindow(setup)
        window.show()
        return app.exec()
    finally:
        sys.excepthook = previous_hook
        diagnostic.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
