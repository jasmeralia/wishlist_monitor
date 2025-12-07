# core/logger.py
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_to_file = os.getenv("LOG_TO_FILE", "true").lower() == "true"
    log_file = os.getenv("LOG_FILE", "/data/wishlist_monitor.log")
    log_max_bytes = int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024)))
    log_backups = int(os.getenv("LOG_BACKUPS", "3"))
    log_to_stdout = os.getenv("LOG_TO_STDOUT", "true").lower() == "true"

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    # Avoid duplicate handlers
    if not root.handlers:
        if log_to_stdout:
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(getattr(logging, log_level, logging.INFO))
            ch.setFormatter(formatter)
            root.addHandler(ch)

        if log_to_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                fh = RotatingFileHandler(
                    log_file,
                    maxBytes=log_max_bytes,
                    backupCount=log_backups,
                )
                fh.setLevel(getattr(logging, log_level, logging.INFO))
                fh.setFormatter(formatter)
                root.addHandler(fh)
            except Exception as e:
                root.warning("Failed to initialize file logging: %s", e)

    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
