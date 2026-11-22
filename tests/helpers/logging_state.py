"""The logging module's state, put back after every test.

A test that runs Alembic in-process (`setup`, a migration suite) runs `env.py`, whose
`logging.config.fileConfig` does what the CLI needs and a test process must not keep:
it replaces the root logger's handlers with one writing to that moment's `sys.stderr`,
which under capture is the test's own stream, closed when the test ends; and, with the
default `disable_existing_loggers=True`, disables every logger that already existed.
Every later test in that worker then logged into a closed stream, or not at all
(`test_logging_state_restored.py`).

Around each test's whole run (setup, call and teardown), this keeps the root logger's
and every existing logger's level, handlers, propagation and `disabled` flag, and the
`logging.disable` level, and puts them back afterwards. Pytest's own capture handlers
come and go inside each phase, so they are in neither copy. A logger first made during
the test is left as made: its module's import configured it, and that stays.

Registered for every suite with `-p logging_state` (pyproject.toml).
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class LoggerState:
    level: int
    handlers: tuple[logging.Handler, ...]
    propagate: bool
    disabled: bool


@dataclass(frozen=True, slots=True)
class LoggingState:
    root: LoggerState
    loggers: dict[str, LoggerState]
    disabled_below: int


def _state_of(logger: logging.Logger) -> LoggerState:
    return LoggerState(
        level=logger.level,
        handlers=tuple(logger.handlers),
        propagate=logger.propagate,
        disabled=logger.disabled,
    )


def _put_back(logger: logging.Logger, state: LoggerState) -> None:
    """Only a logger that changed: `setLevel` empties every logger's level cache."""
    if _state_of(logger) == state:
        return
    if logger.level != state.level:
        logger.setLevel(state.level)
    logger.handlers[:] = state.handlers
    logger.propagate = state.propagate
    logger.disabled = state.disabled


def snapshot() -> LoggingState:
    manager = logging.root.manager
    return LoggingState(
        root=_state_of(logging.root),
        loggers={
            name: _state_of(logger)
            for name, logger in list(manager.loggerDict.items())
            if isinstance(logger, logging.Logger)
        },
        disabled_below=manager.disable,
    )


def restore(state: LoggingState) -> None:
    _put_back(logging.root, state.root)
    for name, logger_state in state.loggers.items():
        _put_back(logging.getLogger(name), logger_state)
    if logging.root.manager.disable != state.disabled_below:
        logging.disable(state.disabled_below)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None]:
    before = snapshot()
    try:
        return (yield)
    finally:
        restore(before)
