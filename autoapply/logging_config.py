"""
Structured logging configuration for AutoApply.

Call setup_logging() once at pipeline startup.
All modules should use: logger = logging.getLogger(__name__)
"""

import logging
import sys
from pathlib import Path

from .config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "autoapply.log"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with file + stdout handlers."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (append mode)
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)
