from __future__ import annotations
import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME
from .logging_setup import install_exception_hook, setup_logging
from .main_window import MainWindow

log = logging.getLogger(__name__)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("LaserWatch")

    log_path = setup_logging()
    install_exception_hook()

    try:
        window = MainWindow()
        window.show()
        log.info("%s started", APP_NAME)
        return app.exec()
    except Exception as exc:
        log.exception("Fatal application startup/runtime error")
        try:
            QMessageBox.critical(
                None,
                APP_NAME,
                f"LaserWatch encountered a fatal error.\n\n{exc}\n\nLog:\n{log_path}"
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
