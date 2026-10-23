"""Automated tests for logging configuration and secret masking."""

import logging
from pathlib import Path
from codepilot.logging_config import SecretMaskingFormatter, get_log_level, setup_logging


def test_get_log_level() -> None:
    """Verify log level resolution from strings and integers."""
    assert get_log_level("DEBUG") == logging.DEBUG
    assert get_log_level("info") == logging.INFO
    assert get_log_level("warning") == logging.WARNING
    assert get_log_level("ERROR") == logging.ERROR
    assert get_log_level("CRITICAL") == logging.CRITICAL
    assert get_log_level(logging.DEBUG) == logging.DEBUG
    assert get_log_level("UNKNOWN_LEVEL") == logging.INFO


def test_secret_masking_formatter() -> None:
    """Verify that credentials and tokens are redacted from formatted logs."""
    formatter = SecretMaskingFormatter("%(message)s")

    record1 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Using api_key: sk-123456789abcdef for request",
        args=(),
        exc_info=None,
    )
    formatted1 = formatter.format(record1)
    assert "sk-123456789abcdef" not in formatted1
    assert "[REDACTED]" in formatted1

    record2 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authorization: Bearer my-secret-jwt-token-xyz",
        args=(),
        exc_info=None,
    )
    formatted2 = formatter.format(record2)
    assert "my-secret-jwt-token-xyz" not in formatted2
    assert "[REDACTED]" in formatted2


def test_setup_logging_with_file(tmp_path: Path) -> None:
    """Verify that setup_logging configures file handler and writes records."""
    log_file = tmp_path / "logs" / "codepilot.log"
    logger = setup_logging(level="DEBUG", log_file=log_file, logger_name="test_logger")

    logger.debug("Test diagnostic log line")
    for handler in logger.handlers:
        handler.flush()
        handler.close()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Test diagnostic log line" in content
    assert "DEBUG" in content
