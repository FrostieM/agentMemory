"""structlog + stdlib logging configuration.

Logs go to **stderr**. This is non-negotiable for the MCP stdio server:
its stdout is owned by the JSON-RPC framing protocol, and any byte that
isn't a valid frame breaks the connection (e.g. sentence-transformers
prints "No device provided, using cpu" on first model load — that
single line is enough to make Claude Code report
"could not connect to MCP server").

stderr is also the 12-factor convention for logs, so it's the right
default for the HTTP service too. `LOG_FORMAT=json` switches the
structlog renderer; `LOG_FORMAT=console` (default) renders compact
key=value lines.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    log_format = os.getenv("LOG_FORMAT", "console").lower()
    log_level = getattr(logging, level.upper(), logging.INFO)

    # `force=True` so this overrides any earlier handler third-party
    # libraries may have installed on the root logger before we got here.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
