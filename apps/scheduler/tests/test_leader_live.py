"""The beat leader lock, against a real Redis (P1-33).

This suite runs the actual Lua the lock ships with. A fake Redis would exercise
the Python around the scripts and not the scripts themselves, and the scripts
are the whole point: they are what stops a stalled leader extending or deleting
a lock its successor now holds.

Two beats are running in the dev stack, so every test uses a key of its own and
removes it afterwards. Nothing here touches the real leadership key.

It fails rather than skips when Redis is absent, for the reason `live.py` gives:
a lock test that quietly did not run looks exactly like one that passed, and
what it is guarding is every customer's checks either stopping or doubling.
"""

from __future__ import annotations

import socket
import time
import uuid
from collections.abc import Iterator
from typing import Final

import pytest
from live import require_stack
from redis import Redis

from nexovue_scheduler.leader import LeaderLock

REDIS_HOST: Final = "localhost"
REDIS_PORT: Final = 16379
REDIS_URL: Final = f"redis://{REDIS_HOST}:{REDIS_PORT}/1"

# Long enough that a slow machine does not expire a lock mid-assertion, short
# enough that the expiry tests do not dominate the suite.
TTL_SECONDS: Final = 2


def _reachable() -> bool:
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the beat leader lock suite",
    remedy=f"start the dev stack so Redis answers on {REDIS_HOST}:{REDIS_PORT} (`just dev`)",
)


@pytest.fixture
def client() -> Iterator[Redis]:
    connection = Redis.from_url(REDIS_URL)
    yield connection
    connection.close()


@pytest.fixture
def key(client: Redis) -> Iterator[str]:
    name = f"nexovue:test:beat-leader:{uuid.uuid4().hex}"
    yield name
    client.delete(name)


def _lock(client: Redis, key: str, ttl: int = TTL_SECONDS) -> LeaderLock:
    return LeaderLock(client, key=key, ttl_seconds=ttl)


def test_only_one_of_two_beats_becomes_leader(client: Redis, key: str) -> None:
    """The whole reason the lock exists: two schedulers, one scheduling."""
    first, second = _lock(client, key), _lock(client, key)

    assert first.acquire() is True
    assert second.acquire() is False
    assert first.held is True
    assert second.held is False


def test_the_leader_keeps_leadership_while_it_refreshes(client: Redis, key: str) -> None:
    """A live leader holds on past the TTL, because it renews inside it."""
    leader = _lock(client, key)
    assert leader.acquire() is True

    deadline = time.monotonic() + TTL_SECONDS * 1.5
    while time.monotonic() < deadline:
        assert leader.keep() is True
        time.sleep(TTL_SECONDS / 4)

    # Still ours, well after the original expiry would have passed.
    assert client.get(key) == leader.token.encode()


def test_a_follower_takes_over_when_the_leader_stops_refreshing(client: Redis, key: str) -> None:
    """The failover this is built for: the leader dies, checks keep running."""
    leader, follower = _lock(client, key), _lock(client, key)
    assert leader.acquire() is True
    assert follower.keep() is False  # nothing to take yet

    time.sleep(TTL_SECONDS + 0.5)  # the leader stops refreshing, so the key expires

    assert follower.keep() is True
    assert client.get(key) == follower.token.encode()


def test_a_stalled_leader_cannot_extend_the_successors_lock(client: Redis, key: str) -> None:
    """The compare-and-set that a naive refresh gets wrong.

    A leader that paused past its TTL is no longer leader. If its next refresh
    blindly extended the key, it would be holding a lock the successor also
    believes it holds — two schedulers, every check fired twice.
    """
    stalled, successor = _lock(client, key), _lock(client, key)
    assert stalled.acquire() is True

    time.sleep(TTL_SECONDS + 0.5)
    assert successor.acquire() is True

    # The stalled process wakes and tries to renew. What matters is that it is
    # told no: a blind refresh would extend the successor's key *without
    # changing its value*, so asserting on the value alone passes while two
    # processes both believe they are leader. Asserting the answer is what
    # catches it — verified by removing the token check and watching this fail.
    assert stalled.keep() is False
    assert stalled.held is False
    assert client.get(key) == successor.token.encode()


def test_a_stalled_leader_cannot_release_the_successors_lock(client: Redis, key: str) -> None:
    """The same compare-and-set on the way out.

    A blind delete here is worse than a blind extend: it would leave *nobody*
    holding the lock while the successor carried on believing it did.
    """
    stalled, successor = _lock(client, key), _lock(client, key)
    assert stalled.acquire() is True

    time.sleep(TTL_SECONDS + 0.5)
    assert successor.acquire() is True

    stalled.release()
    assert client.get(key) == successor.token.encode()


def test_releasing_hands_over_without_waiting_for_the_ttl(client: Redis, key: str) -> None:
    """A clean shutdown should not cost a gap in everyone's checks."""
    leaving, arriving = _lock(client, key), _lock(client, key)
    assert leaving.acquire() is True

    leaving.release()

    assert leaving.held is False
    assert arriving.acquire() is True  # immediately, not one TTL later
