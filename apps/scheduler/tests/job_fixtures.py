"""Tasks that exist only for `test_jobs_live.py`, each leaving a line in a file.

A worker's child imports this module by name, so these run exactly where real
tasks run. A file rather than Postgres, so what a task saw is readable however
the job ended — including by a kill.
"""

from __future__ import annotations

import gc
import os
import signal
import sys
import time
from pathlib import Path

from nexovue_scheduler.jobs.registry import Retry, SoftTimeLimit, attempt, task

NOTE: str = "jobs-test.note"
SLEEP: str = "jobs-test.sleep"
BLOCK: str = "jobs-test.block"
RETRY: str = "jobs-test.retry"
SPAN: str = "jobs-test.span"
DIE: str = "jobs-test.die"
WHO: str = "jobs-test.who"
# F22 AC34: writes the child's `sys.modules`, for `test_connections_have_no_ai.py`.
LOADED: str = "jobs-test.loaded"
GROW: str = "jobs-test.grow"
# F4 AC27: chat's real loop and store, its child killed between two calls or in a call.
CHAT: str = "jobs-test.chat"
# A real task module this suite never names: imported in a child only if it was preloaded.
PRELOADED: str = "nexovue_scheduler.detection.baselines"

# What `grow` keeps, as a job's freed arenas stay with the child that ran it.
_kept: list[bytearray] = []


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


@task(DIE)
def die(*, path: str) -> None:
    """Killed outright mid-job, as the kernel kills a child over its memory limit."""
    _write(path, "dying")
    os.kill(os.getpid(), signal.SIGKILL)


@task(WHO)
def who(*, path: str) -> None:
    """Notes which child ran it, whether the task modules were already imported when it
    was made, and how many objects it inherited frozen."""
    preloaded = PRELOADED in sys.modules
    _write(path, f"pid {os.getpid()} preloaded {preloaded} frozen {gc.get_freeze_count()}")


@task(GROW)
def grow(*, path: str, mebibytes: int) -> None:
    """Keeps `mebibytes` of its own memory after it ends."""
    _kept.append(bytearray(os.urandom(1024)) * (mebibytes * 1024))
    _write(path, f"pid {os.getpid()} grew")


@task(LOADED)
def loaded(*, path: str | None = None) -> None:
    """Records every module this child holds, and how many objects it inherited frozen from
    its forkserver's preload, where a narrow role's suite reads them: at `path`, or where
    the connections suite looks."""
    import json
    import tempfile

    record = {"modules": sorted(sys.modules), "frozen": gc.get_freeze_count()}
    target = Path(path or Path(tempfile.gettempdir()) / "nexovue-connections-child-modules.json")
    target.write_text(json.dumps(record))


@task(CHAT)
def chat(*, path: str, org_id: str, message_id: str, key: str, end: str) -> None:
    """Chat's real loop and Postgres store, with an in-memory provider and API. `end` says
    how the child dies: `between` two provider calls (killed outright, as the kernel kills
    a child), or `blocked` in its first call, deaf to the soft limit until the hard kill.
    Imported here, not at the top: other children of this suite must not load AI code."""
    from datetime import UTC, datetime, timedelta

    from assistant_fakes import RecordingClient
    from nexovue_platform.ai.testing import ScriptedProvider, calls
    from nexovue_platform.checks.sealing import ConfiguredKeys

    from nexovue_scheduler.insight.assistant import LoopParts, answer
    from nexovue_scheduler.insight.assistant_store import PostgresQuestionStore
    from nexovue_scheduler.process_engine import with_engine

    now = datetime.now(UTC)
    window = {
        "from": (now - timedelta(minutes=30)).isoformat(),
        "to": now.isoformat(),
    }

    def second_call(_request: object) -> object:
        _write(path, "killed between calls")
        os.kill(os.getpid(), signal.SIGKILL)
        raise AssertionError("unreachable")

    def blocked_call(_request: object) -> object:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        _write(path, "blocked in a call")
        time.sleep(60)
        raise AssertionError("unreachable")

    replies = [calls("list_services", window), second_call] if end == "between" else [blocked_call]
    provider = ScriptedProvider(replies)  # type: ignore[arg-type]
    with_engine(
        lambda engine: answer(
            org_id,
            message_id,
            LoopParts(
                store=PostgresQuestionStore(engine),
                client=RecordingClient(),
                provider_for=lambda _connection, _key: provider,
                keys=ConfiguredKeys(key, ""),
                clock=lambda: datetime.now(UTC),
            ),
        )
    )
