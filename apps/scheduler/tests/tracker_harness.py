"""Killing a sync worker at a named fault point (F22 plan T4.5; design, *Test harness for
sync*; AC44, AC87).

A sync run is started in a child process, and the child is killed with `SIGKILL` at one
of the design's named points: no `finally`, no rollback but Postgres's own, no reply
read. What the child left behind, in Postgres and in the simulated GitHub, is then what
the next run must finish from, with exactly one issue and at most one comment.

The points inside nexovue's code are reached through `sync.fault`, which only this
harness replaces, and only in the child. `after_tracker_commit_before_reply` is the
tracker's side: the simulated GitHub applies the write, then the parent kills the child
before the reply is read (`kill_after_commit`). Production never imports this module
(`test_connections_tracker_seam.py`).

The child is started by `spawn`, not `fork`, so it holds none of the parent's threads
(the simulated GitHub serves from one) or open database connections.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
from collections.abc import Callable
from typing import Final

from nexovue_platform.checks.sealing import ConfiguredKeys
from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_scheduler.connections import sync
from nexovue_scheduler.connections.github import GitHubEndpoint, GitHubTracker

# The points inside nexovue's code, in the order a creation and a close reach them.
CREATION_POINTS: Final = (
    "before_first_attempt_commit",
    "before_create",
    "after_reply_before_commit",
)
COMMENT_POINTS: Final = ("after_close_before_comment_commit", "after_comment_commit_before_post")
# The tracker's own: the write is applied and the worker dies before reading the reply.
TRACKER_POINT: Final = "after_tracker_commit_before_reply"
CHILD_SECONDS: Final = 60
KILLED: Final = -signal.SIGKILL


def _child(
    dsn: str,
    org_id: str,
    connection_id: str,
    key: str,
    base_url: str,
    point: str | None,
    settles: float,
) -> None:
    def kill_at(name: str) -> None:
        if name == point:
            os.kill(os.getpid(), signal.SIGKILL)

    sync.fault = kill_at

    async def run() -> None:
        engine = create_async_engine(dsn)
        try:
            await sync.sync_connection(
                engine,
                org_id=org_id,
                connection_id=connection_id,
                keys=ConfiguredKeys(key),
                tracker_of=lambda stored: GitHubTracker.for_connection(
                    stored,
                    GitHubEndpoint(base_url=base_url, private_allowed=True),
                    search_settles_seconds=settles,
                ),
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


def run_child(
    *,
    dsn: str,
    org_id: str,
    connection_id: str,
    key: str,
    base_url: str,
    point: str | None,
    settles: float = 0.0,
    started: Callable[[int], None] | None = None,
) -> int | None:
    """Run one sync in a child, killed at `point`; return its exit code (`KILLED` if killed)."""
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_child, args=(dsn, org_id, connection_id, key, base_url, point, settles)
    )
    child.start()
    if started is not None and child.pid is not None:
        started(child.pid)
    child.join(CHILD_SECONDS)
    if child.is_alive():
        child.kill()
        child.join()
        raise AssertionError("the sync child did not finish")
    return child.exitcode


def kill_after_commit(route: str, pid: Callable[[], int | None]) -> Callable[[str], None]:
    """The simulated GitHub's hook: after applying `route`'s write, kill the worker."""

    def hook(name: str) -> None:
        killed = pid()
        if name == route and killed is not None:
            os.kill(killed, signal.SIGKILL)

    return hook
