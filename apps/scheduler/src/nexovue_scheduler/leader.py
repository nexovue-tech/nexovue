"""One beat process schedules; the others wait to take over.

More than one beat runs on purpose. A single scheduler is a single point of
failure, and when it is the thing that decides when checks run, losing it means
every customer's monitoring stops with nothing to say so.

Running two of them naively is worse: Celery's own issue tracker is full of
duplicate-task reports from exactly that, because each beat decides
independently that a schedule is due. So the processes share one Redis key, and
only whoever holds it schedules anything.

The lock is a plain `SET NX PX` against the single Redis this deployment already
runs. Redlock is for locking across independent Redis nodes and would buy
nothing here. What it does need, and what a naive version gets wrong, is that
**refresh and release must check the token first**: a leader that stalled past
the TTL no longer holds the lock, and must not extend or delete the successor's.
"""

from __future__ import annotations

import uuid
from typing import Final

from redis import Redis

# Extend the expiry only while the value is still ours. A leader that paused
# long enough to expire has been replaced, and blindly extending would hand it
# back a lock the successor is already holding — leaving two leaders.
_REFRESH_IF_OURS: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

# Same reasoning for release: never delete a lock we no longer own.
_RELEASE_IF_OURS: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class LeaderLock:
    """Holds beat leadership for one process, for as long as it keeps refreshing.

    Not thread-safe, and does not need to be: Celery's beat loop is a single
    thread, and this is called only from it.
    """

    def __init__(self, client: Redis, key: str, ttl_seconds: int) -> None:
        self._client = client
        self._key = key
        self._ttl_ms = ttl_seconds * 1000
        # Identifies this holder. A fresh value per instance is what lets the
        # scripts above tell our lock from a successor's.
        self._token = uuid.uuid4().hex
        self._held = False
        self._refresh = client.register_script(_REFRESH_IF_OURS)
        self._release = client.register_script(_RELEASE_IF_OURS)

    @property
    def held(self) -> bool:
        """Whether this process believed it was leader at the last check.

        Believed, not is: leadership can lapse between calls if a tick overruns
        the TTL. `keep` is what re-establishes the fact.
        """
        return self._held

    @property
    def token(self) -> str:
        """This holder's identity, for logs and tests."""
        return self._token

    def acquire(self) -> bool:
        """Take leadership if it is free. False means someone else holds it."""
        won = bool(self._client.set(self._key, self._token, nx=True, px=self._ttl_ms))
        self._held = won
        return won

    def keep(self) -> bool:
        """Renew leadership, or take it if it has fallen free.

        Called every poll interval. The renew-or-acquire pair is deliberate:
        after a pause long enough to expire our own key, we are no longer leader
        and must win it again like anyone else rather than assume we still are.
        """
        if self._held and self._refresh(keys=[self._key], args=[self._token, self._ttl_ms]):
            return True
        # Either we never held it, or we held it and let it lapse. Both mean the
        # same thing now: compete for it.
        return self.acquire()

    def release(self) -> None:
        """Give up leadership so a successor can take over without waiting.

        Best-effort. If it fails, the key expires on its own and the only cost
        is a gap of up to one TTL — which is why the TTL is short.
        """
        if not self._held:
            return
        self._held = False
        self._release(keys=[self._key], args=[self._token])
