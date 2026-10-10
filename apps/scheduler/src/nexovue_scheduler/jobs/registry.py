"""Which function runs each task name, and what a task may say back.

A task is a plain function registered under the name its enqueuers use. It runs
in a worker's child process and either returns (done), raises `Retry` (run me
again later), or raises anything else (failed — logged, not retried). Its soft
limit arrives inside it as `SoftTimeLimit`, so a task can let go cleanly; its
hard limit is the child being killed.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Final

# Every module that registers a task. A worker imports them all; beat imports
# none, since it adds jobs by name and never runs one.
TASK_MODULES: Final = (
    "nexovue_scheduler.probes.tasks",
    "nexovue_scheduler.pollers.tasks",
    "nexovue_scheduler.alerts.tasks",
    "nexovue_scheduler.alerts.sweeps",
    "nexovue_scheduler.jobs.expiry",
)

_TASKS: dict[str, Callable[..., None]] = {}
_ATTEMPT: ContextVar[int] = ContextVar("job_attempt", default=1)


class SoftTimeLimit(Exception):  # noqa: N818 — named for what it is, as Celery's was
    """Raised inside a task when its soft limit passes: finish up, the kill is coming."""


class Retry(Exception):  # noqa: N818
    """Raised by a task to be run again after `countdown` seconds, while attempts last."""

    def __init__(self, countdown: float, reason: str) -> None:
        super().__init__(reason)
        self.countdown = countdown


def task(name: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Register a function as the task called `name`."""

    def register(function: Callable[..., None]) -> Callable[..., None]:
        if name in _TASKS:
            raise ValueError(f"two tasks are registered as {name}")
        _TASKS[name] = function
        return function

    return register


def registered(modules: tuple[str, ...] = TASK_MODULES) -> dict[str, Callable[..., None]]:
    """Every task, by name, once each of `modules` is imported."""
    for module in modules:
        importlib.import_module(module)
    return dict(_TASKS)


def attempt() -> int:
    """Which run of its job the current task is: 1 the first time."""
    return _ATTEMPT.get()


def run(function: Callable[..., None], kwargs: dict[str, Any], *, attempt_number: int) -> None:
    """Call a task with its arguments, telling it which attempt this is."""
    token = _ATTEMPT.set(attempt_number)
    try:
        function(**kwargs)
    finally:
        _ATTEMPT.reset(token)
