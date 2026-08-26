"""The one structured logger the API uses, and the stdlib bridge for the rest.

Field names match the Go services exactly, so one query answers "what happened
to this request" across the whole system.

Levels mean the same thing in every service:

``error``
    a human must look at this
``warn``
    degraded, but handled — no action required right now
``info``
    a state change worth a permanent record
``debug``
    development detail; off outside development
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, Final, TextIO

import structlog

from nexovue_scheduler.telemetry.context import (
    FIELD_ENV,
    FIELD_LEVEL,
    FIELD_MESSAGE,
    FIELD_SERVICE,
    FIELD_TIMESTAMP,
)
from nexovue_scheduler.telemetry.redaction import redact_event

FORMAT_JSON: Final = "json"
FORMAT_CONSOLE: Final = "console"

# Python calls it "warning", Go calls it "warn". Go's name wins because it is
# also the shorter one, and the point is that both write the same string.
_LEVEL_ALIASES: Final = {"warning": "warn", "critical": "error"}

_STDLIB_LEVELS: Final = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

# Loggers that ship their own handlers. Left alone they print a second, unformatted
# copy of every line.
_LIBRARY_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine")


def configure_logging(
    *,
    level: str,
    log_format: str,
    service: str,
    env: str,
    stream: TextIO | None = None,
) -> None:
    """Install the structured logger for the process, stdlib logging included.

    ``stream`` exists so a test can read exactly what a real log line looks
    like, rather than inspecting the record before it is rendered.
    """
    shared = _shared_processors(service=service, env=env, log_format=log_format)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Records from libraries have not been through structlog, so they
            # need the same chain applied before rendering.
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.EventRenamer(FIELD_MESSAGE),
                _renderer(log_format),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(_STDLIB_LEVELS[level])
    for name in _LIBRARY_LOGGERS:
        library_logger = logging.getLogger(name)
        library_logger.handlers = []
        library_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A logger for one module, already carrying the service-wide fields."""
    return structlog.stdlib.get_logger(name)


def _shared_processors(
    *, service: str, env: str, log_format: str
) -> list[structlog.typing.Processor]:
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _normalise_level,
        _identity_processor(service=service, env=env),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key=FIELD_TIMESTAMP),
        structlog.processors.StackInfoRenderer(),
    ]
    if log_format == FORMAT_JSON:
        # JSON cannot carry a traceback object; the console renderer prints one
        # far better than this would.
        processors.append(structlog.processors.format_exc_info)
    processors.append(redact_event)
    return processors


def _renderer(log_format: str) -> structlog.typing.Processor:
    if log_format == FORMAT_CONSOLE:
        return structlog.dev.ConsoleRenderer(event_key=FIELD_MESSAGE, timestamp_key=FIELD_TIMESTAMP)
    return structlog.processors.JSONRenderer()


def _normalise_level(
    _logger: Any, _method_name: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Make Python's level names identical to Go's."""
    level = event.get(FIELD_LEVEL)
    if isinstance(level, str):
        event[FIELD_LEVEL] = _LEVEL_ALIASES.get(level, level)
    return event


def _identity_processor(*, service: str, env: str) -> structlog.typing.Processor:
    """Stamp every line with which service, in which environment, wrote it."""

    def processor(
        _logger: Any, _method_name: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event[FIELD_SERVICE] = service
        event[FIELD_ENV] = env
        return event

    return processor
