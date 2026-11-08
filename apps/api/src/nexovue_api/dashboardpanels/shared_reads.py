"""One scan's answer, shared by everyone asking for it within 30 seconds, in one process.

30 people at 30 scans a minute is 900 against an organization's quota of 600, so
without sharing a team cannot watch one dashboard (D27, NFR12). The rules are the
design's table, *The rules for a joined read* (D42):

- **Kept answers.** An `ok` answer is kept for `SHARED_READ_SECONDS`, keyed by tenant,
  project, reader, its arguments and the exact window, so it can never cross a tenant
  or a project. It costs its reader nothing, and carries the time it was really read.
- **Joined reads.** A miss while the same read is in flight waits for it, free.
- **Leading.** Otherwise the request takes a process-cap place, checks again after
  that wait, pays through its `ScanCharge`, and only then creates the in-flight entry.
  So a refusal comes before the entry exists, and is never shared (AC87).
- **The statement belongs to a task, not a request.** Its limit is fixed, so a leader
  near its deadline never hands a joiner a timeout (AC88). A reader whose deadline
  passes reads `timed_out` and the task carries on for the rest; with no reader left
  it is cancelled, which closes its connection. It holds its cap place until it ends.
- **A failed shared read** is seen by its joiners, each of which tries once more if a
  second remains. A failure is never kept.

**Bounded by rows, not entries** (D42): at most `SHARED_READ_MAX_ROWS` in all and
`SHARED_READ_ORGANIZATION_ROWS` for one organization, and an answer over
`SHARED_READ_ANSWER_ROWS` is not kept; expired entries go first, then the least
recently used. Every number is a *guess* until plan T5.7 measures it.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal, Protocol

from nexovue_platform.telemetry.logging import get_logger

from nexovue_api.dashboardpanels.caps import CAP_WAIT_SECONDS, ScanCaps
from nexovue_api.dashboards.compile import ScanKey
from nexovue_api.data.clickhouse.base import server_error_code
from nexovue_api.ratelimit.charge import Refused

logger = get_logger(__name__)

SHARED_READ_SECONDS: Final = 30
SHARED_READ_MAX_ROWS: Final = 100_000
SHARED_READ_ORGANIZATION_ROWS: Final = 25_000
SHARED_READ_ANSWER_ROWS: Final = 2_000
# One fixed limit for every shared statement, never one reader's remaining time.
SHARED_READ_STATEMENT_SECONDS: Final = 15
# With less than this left, no scan starts: it would only read `timed_out` (AC92).
MIN_SECONDS_TO_START: Final = 1.0

# ClickHouse's error codes (`src/Common/ErrorCodes.cpp`), mapped ahead of the broad
# catch: a server-side refusal is a refusal, and its own timeout is a timeout (AC91, AC92).
TIMEOUT_EXCEEDED: Final = 159
TOO_MANY_SIMULTANEOUS_QUERIES: Final = 202

Source = Literal["kept", "joined", "read"]


@dataclass(frozen=True, slots=True)
class ReadKey:
    """What makes two reads the same read: tenant, project, reader, arguments, window."""

    org_id: str
    project_id: str
    scan: ScanKey
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True, slots=True)
class Answer:
    value: Any
    rows: int
    # When the store was really read, so a page's age tells the truth (design section 6).
    read_at: datetime


@dataclass(frozen=True, slots=True)
class Served:
    answer: Answer
    source: Source


@dataclass(frozen=True, slots=True)
class RefusedRead:
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class TimedOutRead:
    pass


@dataclass(frozen=True, slots=True)
class FailedRead:
    pass


Outcome = Served | RefusedRead | TimedOutRead | FailedRead
Statement = Callable[[], Awaitable[Answer]]


class Charge(Protocol):
    async def claim(self) -> Refused | None: ...


@dataclass(slots=True)
class _Flight:
    task: asyncio.Task[Answer]
    waiters: int = 0


@dataclass(frozen=True, slots=True)
class _Kept:
    answer: Answer
    expires_at: float
    cost: int


@dataclass(slots=True)
class _Store:
    entries: OrderedDict[ReadKey, _Kept] = field(default_factory=OrderedDict)
    rows: int = 0
    rows_by_organization: Counter[str] = field(default_factory=Counter)


class SharedReads:
    """The process's kept answers and reads in flight. One per API process."""

    def __init__(self, *, caps: ScanCaps, clock: Callable[[], float] = time.monotonic) -> None:
        self._caps = caps
        self._clock = clock
        self._store = _Store()
        self._flights: dict[ReadKey, _Flight] = {}

    def now(self) -> float:
        """The clock deadlines are counted on: the middleware's arrival clock."""
        return self._clock()

    def rows_kept(self, org_id: str | None = None) -> int:
        if org_id is None:
            return self._store.rows
        return self._store.rows_by_organization[org_id]

    async def read(
        self, key: ReadKey, *, run: Statement, raw: bool, charge: Charge, deadline: float
    ) -> Outcome:
        """One panel's scan: kept, joined or read, or the honest reason it was not."""
        outcome, joined_a_failure = await self._attempt(
            key, run=run, raw=raw, charge=charge, deadline=deadline
        )
        if joined_a_failure and deadline - self.now() >= MIN_SECONDS_TO_START:
            outcome, _ = await self._attempt(
                key, run=run, raw=raw, charge=charge, deadline=deadline
            )
        return outcome

    async def _attempt(
        self, key: ReadKey, *, run: Statement, raw: bool, charge: Charge, deadline: float
    ) -> tuple[Outcome, bool]:
        """One try, and whether it joined a read that failed, which earns one more."""
        if (kept := self._kept(key)) is not None:
            return Served(kept, "kept"), False
        if (flight := self._flights.get(key)) is not None:
            return await self._wait(flight, deadline, source="joined")

        remaining = deadline - self.now()
        if remaining < MIN_SECONDS_TO_START:
            return TimedOutRead(), False
        place = await self._caps.acquire(
            key.org_id,
            raw=raw,
            wait_seconds=min(CAP_WAIT_SECONDS, remaining - MIN_SECONDS_TO_START),
        )
        if place is None:
            if deadline - self.now() < MIN_SECONDS_TO_START:
                return TimedOutRead(), False
            # A place frees at the latest when a running statement reaches its limit.
            return RefusedRead(retry_after_seconds=SHARED_READ_STATEMENT_SECONDS), False

        # The wait may have let another reader lead, or finish.
        if (kept := self._kept(key)) is not None:
            place.release()
            return Served(kept, "kept"), False
        if (flight := self._flights.get(key)) is not None:
            place.release()
            return await self._wait(flight, deadline, source="joined")
        if (refused := await charge.claim()) is not None:
            place.release()
            return RefusedRead(retry_after_seconds=refused.retry_after_seconds), False

        task = asyncio.create_task(self._lead(key, run, place.release))
        task.add_done_callback(_retrieve)
        flight = _Flight(task=task)
        self._flights[key] = flight
        outcome, _ = await self._wait(flight, deadline, source="read")
        return outcome, False

    async def _lead(self, key: ReadKey, run: Statement, release: Callable[[], None]) -> Answer:
        """The statement itself, owned by no request."""
        try:
            answer = await run()
        except Exception as error:
            # Logged once here, with its reader, and never with the definition (design,
            # *Observability*). Each waiter maps it to its panel's state.
            logger.warning(
                "a dashboard scan failed",
                reader=key.scan.reader,
                clickhouse_code=server_error_code(error),
                error=type(error).__name__,
            )
            raise
        finally:
            release()
            self._flights.pop(key, None)
        self._keep(key, answer)
        return answer

    async def _wait(
        self, flight: _Flight, deadline: float, *, source: Source
    ) -> tuple[Outcome, bool]:
        """Wait for a read in flight until this reader's own deadline."""
        flight.waiters += 1
        try:
            answer = await asyncio.wait_for(
                asyncio.shield(flight.task), timeout=max(deadline - self.now(), 0.0)
            )
        except TimeoutError:
            return TimedOutRead(), False
        except Exception as error:
            # The panel boundary: one scan's fault must never take the page.
            return _state_of(error), True
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                # Nobody is left to read it: closing the connection stops it early, if
                # ClickHouse honours the cancel setting, and at its fixed limit if not.
                flight.task.cancel()
        return Served(answer, source), False

    def _kept(self, key: ReadKey) -> Answer | None:
        self._expire()
        entry = self._store.entries.get(key)
        if entry is None:
            return None
        self._store.entries.move_to_end(key)
        return entry.answer

    def _keep(self, key: ReadKey, answer: Answer) -> None:
        if answer.rows > SHARED_READ_ANSWER_ROWS:
            return
        self._expire()
        # An empty answer still takes a place, so entries are bounded too.
        cost = max(1, answer.rows)
        self._drop(key)
        while self._store.rows + cost > SHARED_READ_MAX_ROWS:
            self._drop(next(iter(self._store.entries)))
        while self._store.rows_by_organization[key.org_id] + cost > SHARED_READ_ORGANIZATION_ROWS:
            self._drop(next(k for k in self._store.entries if k.org_id == key.org_id))
        self._store.entries[key] = _Kept(
            answer=answer, expires_at=self.now() + SHARED_READ_SECONDS, cost=cost
        )
        self._store.rows += cost
        self._store.rows_by_organization[key.org_id] += cost

    def _expire(self) -> None:
        now = self.now()
        for key in [key for key, entry in self._store.entries.items() if entry.expires_at <= now]:
            self._drop(key)

    def _drop(self, key: ReadKey) -> None:
        entry = self._store.entries.pop(key, None)
        if entry is None:
            return
        self._store.rows -= entry.cost
        self._store.rows_by_organization[key.org_id] -= entry.cost
        if self._store.rows_by_organization[key.org_id] <= 0:
            del self._store.rows_by_organization[key.org_id]


def _state_of(error: BaseException) -> Outcome:
    code = server_error_code(error)
    if code == TIMEOUT_EXCEEDED:
        return TimedOutRead()
    if code == TOO_MANY_SIMULTANEOUS_QUERIES:
        return RefusedRead(retry_after_seconds=SHARED_READ_STATEMENT_SECONDS)
    return FailedRead()


def _retrieve(task: asyncio.Task[Answer]) -> None:
    """Collect a task's failure even when every reader has gone, so none goes unretrieved."""
    if not task.cancelled():
        task.exception()
