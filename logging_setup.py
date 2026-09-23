"""
LOQI — logging setup.

One place to configure logging for the whole app: a readable console handler
(keeps LOQI's emoji-friendly, low-noise console feel) plus a rotating file
handler that captures full detail to ``logs/loqi.log`` for debugging.

Usage::

    from logging_setup import setup_logging, get_logger

    setup_logging()                 # once, at process start (idempotent)
    log = get_logger(__name__)
    log.info("🎤 listening…")

``print()`` call sites are migrated to this incrementally; both can coexist.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR

_CONSOLE_FMT = "%(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_LOG_FILE = LOGS_DIR / "loqi.log"

_configured = False


def _make_console_handler(level: int) -> logging.Handler:
    """Console handler on stdout, tolerant of emoji on Windows code pages."""
    stream = sys.stdout
    # On Windows the console may be a legacy code page (cp1252) that raises
    # UnicodeEncodeError on emoji. Reconfigure to utf-8 with replacement so a
    # stray glyph never crashes the app.
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_CONSOLE_FMT))
    return handler


def _make_file_handler(level: int) -> logging.Handler:
    """Rotating file handler: 1 MB × 3 backups, full detail."""
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FILE_FMT))
    return handler


def setup_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> None:
    """Configure the root logger. Idempotent — safe to call more than once."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(min(console_level, file_level))
    root.addHandler(_make_console_handler(console_level))
    try:
        root.addHandler(_make_file_handler(file_level))
    except OSError:
        # A locked/unavailable log file must never stop the assistant.
        root.warning("Could not open log file %s; console logging only.", _LOG_FILE)

    # Quiet down noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "urllib3", "numba", "openwakeword"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured first."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
