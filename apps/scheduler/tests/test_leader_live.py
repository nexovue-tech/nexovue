"""The beat leader lock, against a real Postgres (P1-33, ADR-020).

Leadership is an advisory lock held by a connection, so what must be proved is
what Postgres does with it: one holder at a time, and the lock freed the moment
its holder's connection goes — which is how a beat that dies hands over without
anyone noticing it died.

Two beats run in the dev stack, so every test uses a lock id of its own and the
real leadership lock is never touched. It fails rather than skips when Postgres
is absent, for the reason `live.py` gives.
"""

from __future__ import annotations

import os
import random
import socket
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack

from nexovue_scheduler.leader import BEAT_LEADER_LOCK, LeaderLock

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the beat leader lock suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
async def locks() -> AsyncIterator[tuple[LeaderLock, LeaderLock]]:
    """Two would-be leaders contending for a lock nothing else uses."""
    lock_id = random.randint(1, 2**62)
    assert lock_id != BEAT_LEADER_LOCK
    first, second = LeaderLock(DSN, lock_id=lock_id), LeaderLock(DSN, lock_id=lock_id)
    yield first, second
    await first.release()
    await second.release()


async def test_one_of_two_leads(locks: tuple[LeaderLock, LeaderLock]) -> None:
    first, second = locks

    assert await first.keep()
    assert not await second.keep()
    # Holding it is not taking it twice: the leader stays the leader.
    assert await first.keep()
    assert not await second.keep()


async def test_releasing_hands_leadership_on(locks: tuple[LeaderLock, LeaderLock]) -> None:
    first, second = locks
    await first.keep()

    await first.release()

    assert not first.held
    assert await second.keep()


async def test_a_leader_whose_connection_dies_gives_the_lock_up(
    locks: tuple[LeaderLock, LeaderLock],
) -> None:
    """The failure the old Redis lock needed an expiry and two scripts for. Here
    the process dying is its connection dying, and Postgres frees the lock then."""
    first, second = locks
    await first.keep()
    assert first._connection is not None

    first._connection.terminate()

    assert await second.keep()
    # The old leader learns it has lost, rather than believing it still leads.
    assert not await first.keep()
