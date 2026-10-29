"""Logging configuration for CyberPulse AI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | str = "logs",
    logger_name: str | None = None,
) -> logging.Logger:
    """Configure console and rotating file logging.

    Args:
        log_level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_dir: Directory where the rotating log file will be saved.
        logger_name: Name of the logger to configure. If None, configures root logger.

    Returns:
        The configured logger instance.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called repeatedly
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler
    target_log_dir = Path(log_dir)
    target_log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = target_log_dir / "cyberpulse.log"

    file_handler = RotatingFileHandler(
        filename=str(log_file_path),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
