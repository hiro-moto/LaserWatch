from __future__ import annotations
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from . import APP_NAME


def setup_logging() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    log_dir = Path(base or ".") / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "LaserWatch.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
        )

        fh = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    logging.getLogger(__name__).info("Logging initialized: %s", log_path)
    return log_path


def install_exception_hook():
    logger = logging.getLogger("laserwatch.unhandled")

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = hook
