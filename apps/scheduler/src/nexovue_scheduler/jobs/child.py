"""The child process a worker runs jobs in, one at a time.

A child, not a thread, for the reason Celery's prefork pool used one: a probe can
hang inside libcurl or a customer's database driver where nothing in Python can
interrupt it, and only killing the process frees the slot. The child is started
with `spawn`, so it shares no socket, thread or event loop with the worker that
started it; it opens its own Kafka producer and engine on first use, as the
task modules already do.

The soft limit is a `SIGALRM` that raises `SoftTimeLimit` inside the task — which
interrupts Python code, not a call blocked in C. That case reaches the hard limit,
where the worker kills this process.
"""

from __future__ import annotations

import signal
import traceback
from multiprocessing.connection import Connection
from types import FrameType
from typing import Any, Final

from nexovue_platform.telemetry.logging import configure_logging, get_logger

from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit
from nexovue_scheduler.settings import SERVICE_NAME, get_settings

logger = get_logger(__name__)

# What a child reports for one job.
DONE: Final = "done"
RETRY: Final = "retry"
FAILED: Final = "failed"


def serve(connection: Connection, modules: tuple[str, ...] = registry.TASK_MODULES) -> None:
    """Run jobs sent down `connection` until it closes, from the tasks in `modules`."""
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=SERVICE_NAME,
        env=settings.env,
    )
    # The worker owns shutdown: a Ctrl-C reaching the whole process group must
    # not kill a job mid-write before the worker decides what to do with it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGALRM, _soft_limit_reached)
    tasks = registry.registered(modules)

    while True:
        try:
            message = connection.recv()
        except EOFError:
            return
        name, kwargs, soft_limit_seconds, attempt = message
        connection.send(_run(tasks, name, kwargs, soft_limit_seconds, attempt))


def _run(
    tasks: dict[str, Any], name: str, kwargs: dict[str, Any], soft_limit: int, attempt: int
) -> tuple[Any, ...]:
    function = tasks.get(name)
    if function is None:
        return (FAILED, f"no task is registered as {name}")
    signal.setitimer(signal.ITIMER_REAL, soft_limit)
    try:
        registry.run(function, kwargs, attempt_number=attempt)
    except Retry as retry:
        return (RETRY, retry.countdown, str(retry))
    except SoftTimeLimit:
        logger.error("a job passed its soft time limit", task=name, soft_limit_seconds=soft_limit)
        return (FAILED, f"passed its soft time limit of {soft_limit}s")
    except Exception as failure:
        logger.exception("a job failed", task=name)
        return (FAILED, "".join(traceback.format_exception_only(failure)).strip())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return (DONE,)


def _soft_limit_reached(_signal: int, _frame: FrameType | None) -> None:
    raise SoftTimeLimit
