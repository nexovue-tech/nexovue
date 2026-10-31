"""Tasks that exist only for `test_jobs_live.py`, each leaving a line in a file.

A worker's child imports this module by name, so these run exactly where real
tasks run. A file rather than Postgres, so what a task saw is readable however
the job ended — including by a kill.
"""

from __future__ import annotations

import signal
import time
from pathlib import Path

from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit, attempt, task

NOTE: str = "jobs-test.note"
SLEEP: str = "jobs-test.sleep"
BLOCK: str = "jobs-test.block"
RETRY: str = "jobs-test.retry"
SPAN: str = "jobs-test.span"


def _write(path: str, line: str) -> None:
    with Path(path).open("a", encoding="utf-8") as notes:
        notes.write(f"{line}\n")


@task(NOTE)
def note(*, path: str, marker: str, pause: float = 0) -> None:
    """Leaves its marker, after `pause` — long enough, if asked, to still be running
    when another worker polls."""
    time.sleep(pause)
    _write(path, marker)


@task(SLEEP)
def sleep(*, path: str, seconds: float) -> None:
    """Runs past its soft limit, and cleans up when told to stop."""
    try:
        time.sleep(seconds)
    except SoftTimeLimit:
        _write(path, "cleaned up")
        raise
    _write(path, "slept")


@task(BLOCK)
def block(*, path: str, seconds: float) -> None:
    """Deaf to its soft limit, as a call blocked in C is: only the kill ends it."""
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    _write(path, "blocking")
    time.sleep(seconds)
    _write(path, "unblocked")


@task(RETRY)
def retry(*, path: str, succeed_on: int) -> None:
    _write(path, f"attempt {attempt()} at {time.monotonic()}")
    if attempt() < succeed_on:
        raise Retry(countdown=1, reason="not yet")


@task(SPAN)
def span(*, path: str, marker: str, pause: float) -> None:
    """Notes when it starts and ends, so a test can count how many ran at once."""
    _write(path, f"start {marker}")
    time.sleep(pause)
    _write(path, f"end {marker}")
