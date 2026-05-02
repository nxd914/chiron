"""
Logging configuration for the Kinzie trading system.

Plain logging by default (preserves existing behavior).
Set LOG_FORMAT=json for structured JSON output via structlog.
"""

from __future__ import annotations

import logging
import os
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure root logger. Call once at daemon startup.

    Environment:
        LOG_FORMAT=json  → structured JSON via structlog (requires: pip install structlog)
        LOG_FORMAT=plain → standard format (default)
        LOG_LEVEL=DEBUG  → override level
    """
    log_format = os.environ.get("LOG_FORMAT", "plain").lower()
    level_name = os.environ.get("LOG_LEVEL", "").upper()
    if level_name:
        level = getattr(logging, level_name, level)

    if log_format == "json":
        _configure_structlog(level)
    else:
        _configure_plain(level)


def _configure_plain(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def _configure_structlog(level: int) -> None:
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        logging.basicConfig(
            level=level,
            format="%(message)s",
            stream=sys.stdout,
        )
    except ImportError:
        _configure_plain(level)
        logging.getLogger(__name__).warning(
            "structlog not installed — falling back to plain logging. "
            "Install with: pip install structlog"
        )
