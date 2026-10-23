"""Logging configuration for CodePilot AI.

Provides consistent log formatting, configurable levels, secret masking,
and file/stream handler management.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Union

# Sensitive keyword patterns to sanitize from diagnostic logs
SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key[\s:=]+)(['\"][^'\"]+['\"]|\S+)", re.IGNORECASE),
    re.compile(r"(token[\s:=]+)(['\"][^'\"]+['\"]|\S+)", re.IGNORECASE),
    re.compile(r"(secret[\s:=]+)(['\"][^'\"]+['\"]|\S+)", re.IGNORECASE),
    re.compile(r"(password[\s:=]+)(['\"][^'\"]+['\"]|\S+)", re.IGNORECASE),
    re.compile(r"(bearer\s+)([a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
]

DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class SecretMaskingFormatter(logging.Formatter):
    """Logging formatter that redacts secrets and sensitive credentials."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        sanitized = original
        for pattern in SENSITIVE_PATTERNS:
            sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
        return sanitized


def get_log_level(level_name: Union[str, int]) -> int:
    """Convert string or int log level to standard logging level integer."""
    if isinstance(level_name, int):
        return level_name
    normalized = str(level_name).strip().upper()
    level = getattr(logging, normalized, None)
    if isinstance(level, int):
        return level
    return logging.INFO


def setup_logging(
    level: Union[str, int] = "INFO",
    log_file: Optional[Path] = None,
    logger_name: str = "codepilot",
) -> logging.Logger:
    """Configure and return the primary application logger.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path to write diagnostic log entries.
        logger_name: Name of the logger to configure.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(logger_name)
    resolved_level = get_log_level(level)
    logger.setLevel(resolved_level)

    # Clear existing handlers to prevent duplicate logs on re-initialization
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = SecretMaskingFormatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # Standard stream handler (writes to sys.stderr so stdout stays clean for CLI)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(resolved_level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Optional file handler for diagnostics
    if log_file:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
            file_handler.setLevel(resolved_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            logger.warning("Could not initialize log file at %s: %s", log_file, e)

    # Do not propagate to root logger to avoid double logging
    logger.propagate = False
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a child logger under the 'codepilot' namespace."""
    if not name:
        return logging.getLogger("codepilot")
    if name.startswith("codepilot"):
        return logging.getLogger(name)
    return logging.getLogger(f"codepilot.{name}")
