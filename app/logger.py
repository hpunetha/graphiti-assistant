"""
logger.py — Centralised logging configuration for MedBook.

Creates two handlers:
  - Console (stdout): INFO and above, plain text
  - File (logs/medbook_YYYYMMDD.log): DEBUG and above, with timestamps

Usage anywhere in the app:
    from app.logger import get_logger
    log = get_logger(__name__)
    log.info("Patient identified: %s", phone)
    log.debug("Tool args: %s", args)
    log.error("DB error: %s", exc)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def _setup_logging() -> None:
    """Configure root logger once. Subsequent calls are no-ops."""
    root = logging.getLogger()
    if root.handlers:
        return  # Already configured

    root.setLevel(logging.DEBUG)

    # ── File handler — DEBUG+ with full timestamps ──────────────────────
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"medbook_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # ── Console handler — INFO+ compact ─────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        fmt="%(levelname)-8s %(name)s: %(message)s"
    ))

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Keep third-party chatter quiet (Graphiti & Neo4j are already silenced
    # in memory.py; silence them at the root level too for clean log files)
    logging.getLogger("graphiti_core").setLevel(logging.CRITICAL)
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, initialising the root config on first call."""
    _setup_logging()
    return logging.getLogger(name)
