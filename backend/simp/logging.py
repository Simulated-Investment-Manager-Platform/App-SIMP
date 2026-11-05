"""Structured JSON logs (structlog) with run_id and session date bound via contextvars."""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging() -> None:
    renderer = (
        structlog.processors.JSONRenderer()
        if os.environ.get("SIMP_LOG_FORMAT", "json") == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
