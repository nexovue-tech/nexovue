"""The live tail: tenancy, the gap it admits to, and what a closed tab releases.

Three properties are held here, and they are the three that make a tail either
trustworthy or worthless:

1. the tenant comes from the session and can never come from a message;
2. a viewer that falls behind loses the *oldest* lines and is told how many;
3. the last viewer to leave takes the feed's poll loop with it.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import TENANT_BINDING, TenantScopedRepository
from nexovue_api.logs import tail
from nexovue_api.logs.search import LogFilters

TENANT_A = "org-a"
TENANT_B = "org-b"
PROJECT = "project-1"

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
# Arrivals are stamped near "now": a tail starts one primer window back, and a
# record older than that has already been passed.
ARRIVED = int(NOW.timestamp() * 1_000_000_000)

_TAIL_COLUMNS = [
    "observed_nanos",
    "arrival_nanos",
    "row_key",
    "service_name",
    "severity",
    "severity_number",
    "body",
    "trace_ref",
    "span_ref",
    "attributes_json",
]


def _line(
    *,
    arrival_nanos: int,
    row_key: int,
    body: str = "payment authorized",
    service: str = "demo-payments",
    severity: str = "INFO",
    severity_number: int = 9,
) -> tuple[Any, ...]:
    return (
        arrival_nanos,
        arrival_nanos,
        row_key,
        service,
        severity,
        severity_number,
        body,
        b"aa" * 16,
        b"bb11bb11bb11bb11",
        "{}",
    )


def _arrival(**line: Any) -> tail.ArrivedLog:
    return tail.to_arrival(dict(zip(_TAIL_COLUMNS, _line(**line), strict=True)))


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeTailStore:
    """Serves each scripted poll in turn, and only to the tenant that owns them."""

    def __init__(self, polls: list[list[tuple[Any, ...]]], owner: str = TENANT_A) -> None:
        self._polls = polls
        self._owner = owner
        self.bound_tenants: list[str] = []
        self.bound_since: list[datetime] = []

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        bound = dict(parameters or {})
        self.bound_tenants.append(bound["tenant_id"])
        self.bound_since.append(bound["since"])

        if bound["tenant_id"] != self._owner or not self._polls:
            return FakeQueryResult([], [])
        return FakeQueryResult(_TAIL_COLUMNS, self._polls.pop(0))


def _repository(tenant: str, store: FakeTailStore) -> TenantScopedRepository:
    return TenantScopedRepository(store, tenant_id=tenant)  # type: ignore[arg-type]


# ------------------------------------------------------------------- tenancy


def test_the_only_words_a_subscriber_may_speak_are_pause_and_resume() -> None:
    assert tail.interpret_command({"action": "pause"}) == tail.PAUSE
    assert tail.interpret_command({"action": "resume"}) == tail.RESUME


@pytest.mark.parametrize(
    "message",
    [
        {"action": "subscribe", "tenant_id": "org-b"},
        {"tenant_id": "org-b"},
        {"action": "pause", "tenant_id": "org-b", "project_id": "someone-elses"},
        {"action": 17},
        "pause",
        None,
        [],
    ],
    ids=[
        "a subscribe carrying a tenant",
        "a bare tenant",
        "a valid action smuggling a tenant",
        "a non-string action",
        "a bare string",
        "null",
        "a list",
    ],
)
def test_no_message_can_name_a_tenant_or_a_project(message: object) -> None:
    """A subscription is a read, and a read never takes its tenant from a caller.

    `interpret_command` is the whole of what a connected client can influence, so
    a tenant that cannot survive this function cannot reach a query. The two
    fields are not rejected — they are unreachable.
    """
    action = tail.interpret_command(message)
    assert action in (None, tail.PAUSE, tail.RESUME)


async def test_a_feed_reads_only_the_tenant_its_repository_was_built_for() -> None:
    """The proof that the binding survives the whole path, not just the parser."""
    store = FakeTailStore([[_line(arrival_nanos=ARRIVED, row_key=1)]], owner=TENANT_A)
    intruder = tail.TailFeed(
        _repository(TENANT_B, store),
        project_id=PROJECT,
        on_idle=lambda: None,
        poll_interval_seconds=0,
        now=lambda: NOW,
    )
    viewer = intruder.attach(LogFilters())
    await asyncio.sleep(0.05)

    assert store.bound_tenants and set(store.bound_tenants) == {TENANT_B}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(viewer.next_batch(), timeout=0.05)
    viewer.close()


def test_the_tail_statement_binds_the_tenant() -> None:
    assert TENANT_BINDING in tail.TAIL_SQL


# ------------------------------------------------------------ the admitted gap


def test_a_buffer_below_capacity_drops_nothing() -> None:
    buffer = tail.ViewerBuffer(capacity=3)
    buffer.offer([_arrival(arrival_nanos=n, row_key=n) for n in (1, 2)])

    drained = buffer.drain()
    assert len(drained.records) == 2
    assert drained.dropped == 0


def test_a_full_buffer_drops_the_oldest_and_reports_how_many() -> None:
    buffer = tail.ViewerBuffer(capacity=3)
    buffer.offer([_arrival(arrival_nanos=n, row_key=n, body=f"line {n}") for n in range(1, 6)])

    drained = buffer.drain()

    assert [held.line.body for held in drained.records] == ["line 3", "line 4", "line 5"]
    assert drained.dropped == 2
    assert buffer.dropped_total == 2


def test_the_dropped_count_is_per_frame_not_cumulative() -> None:
    """The screen places a gap between two frames; it needs the size of that gap."""
    buffer = tail.ViewerBuffer(capacity=2)
    buffer.offer([_arrival(arrival_nanos=n, row_key=n) for n in range(1, 5)])
    assert buffer.drain().dropped == 2

    buffer.offer([_arrival(arrival_nanos=n, row_key=n) for n in range(5, 8)])
    assert buffer.drain().dropped == 1
    assert buffer.dropped_total == 3


def test_a_buffer_that_could_hold_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot report a gap"):
        tail.ViewerBuffer(capacity=0)


async def test_a_paused_viewer_keeps_buffering_and_reports_the_gap_on_resume() -> None:
    """Pausing is the ordinary way a viewer falls behind, so it is the way it is proved."""
    viewer = tail.TailViewer(LogFilters(), release=lambda _: None, capacity=3)
    viewer.pause()
    viewer.offer([_arrival(arrival_nanos=n, row_key=n, body=f"line {n}") for n in range(1, 6)])

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(viewer.next_batch(), timeout=0.05)

    viewer.resume()
    batch = await asyncio.wait_for(viewer.next_batch(), timeout=0.5)

    assert [held.line.body for held in batch.records] == ["line 3", "line 4", "line 5"]
    assert batch.dropped == 2


async def test_a_viewer_sees_only_the_lines_its_filters_admit() -> None:
    viewer = tail.TailViewer(LogFilters(min_severity=17), release=lambda _: None)
    viewer.offer(
        [
            _arrival(arrival_nanos=1, row_key=1, severity="INFO", severity_number=9),
            _arrival(arrival_nanos=2, row_key=2, severity="ERROR", severity_number=17, body="boom"),
        ]
    )

    batch = await asyncio.wait_for(viewer.next_batch(), timeout=0.5)
    assert [held.line.body for held in batch.records] == ["boom"]


def test_a_filter_matches_a_body_substring_case_insensitively() -> None:
    line = _arrival(arrival_nanos=1, row_key=1, body="Payment Authorization Failed")

    assert tail.matches(line, LogFilters(search="author"))
    assert not tail.matches(line, LogFilters(search="declined"))
    assert tail.matches(line, LogFilters(service_name="demo-payments"))
    assert not tail.matches(line, LogFilters(service_name="demo-checkout"))


# ------------------------------------------------------------------ the cursor


def test_the_cursor_delivers_a_batch_sharing_one_arrival_instant_exactly_once() -> None:
    """The writer stamps a whole Kafka batch with one `received_at`.

    A strict `>` on the last arrival would skip every record after the first in
    such a batch; an inclusive read without the memory would send them all twice.
    """
    cursor = tail.TailCursor(since_nanos=0)
    batch = [_arrival(arrival_nanos=100, row_key=key) for key in (1, 2, 3)]

    assert len(cursor.unseen(batch)) == 3
    assert cursor.since_nanos == 100
    assert cursor.unseen(batch) == []


def test_the_cursor_advances_past_a_newer_arrival_and_forgets_the_older_batch() -> None:
    cursor = tail.TailCursor(since_nanos=0)
    cursor.unseen([_arrival(arrival_nanos=100, row_key=key) for key in (1, 2)])

    later = cursor.unseen(
        [
            _arrival(arrival_nanos=100, row_key=1),
            _arrival(arrival_nanos=200, row_key=9),
        ]
    )

    assert [held.row_key for held in later] == [9]
    assert cursor.since_nanos == 200


async def test_a_feed_delivers_each_line_once_across_polls() -> None:
    store = FakeTailStore(
        [
            [_line(arrival_nanos=ARRIVED, row_key=1), _line(arrival_nanos=ARRIVED, row_key=2)],
            [_line(arrival_nanos=ARRIVED, row_key=2), _line(arrival_nanos=ARRIVED + 1, row_key=3)],
        ]
    )
    feed = tail.TailFeed(
        _repository(TENANT_A, store),
        project_id=PROJECT,
        on_idle=lambda: None,
        poll_interval_seconds=0,
        now=lambda: NOW,
    )
    viewer = feed.attach(LogFilters())

    first = await asyncio.wait_for(viewer.next_batch(), timeout=1)
    second = await asyncio.wait_for(viewer.next_batch(), timeout=1)
    viewer.close()

    assert [held.row_key for held in first.records] == [1, 2]
    assert [held.row_key for held in second.records] == [3]


async def test_a_feed_starts_at_the_primer_window_rather_than_at_this_instant() -> None:
    """Ingest is not instantaneous; a tail starting at `now` reads as broken."""
    store = FakeTailStore([[]])
    feed = tail.TailFeed(
        _repository(TENANT_A, store),
        project_id=PROJECT,
        on_idle=lambda: None,
        poll_interval_seconds=0,
        now=lambda: NOW,
    )
    viewer = feed.attach(LogFilters())
    await asyncio.sleep(0.05)
    viewer.close()

    assert abs(store.bound_since[0] - (NOW - tail.PRIMER_WINDOW)) < timedelta(seconds=1)


# --------------------------------------------------------- releasing on close


async def test_the_last_viewer_to_leave_stops_the_poll_loop() -> None:
    store = FakeTailStore([])
    released: list[str] = []
    feed = tail.TailFeed(
        _repository(TENANT_A, store),
        project_id=PROJECT,
        on_idle=lambda: released.append(PROJECT),
        poll_interval_seconds=0.01,
        now=lambda: NOW,
    )

    first = feed.attach(LogFilters())
    second = feed.attach(LogFilters())
    assert feed.running

    first.close()
    assert feed.viewer_count == 1
    assert feed.running
    assert released == []

    second.close()
    await asyncio.sleep(0)

    assert feed.viewer_count == 0
    assert released == [PROJECT]
    assert not feed.running


async def test_closing_twice_releases_once() -> None:
    """A disconnect and a `finally` both call close; the second must be a no-op."""
    feed = tail.TailFeed(
        _repository(TENANT_A, FakeTailStore([])),
        project_id=PROJECT,
        on_idle=lambda: None,
        poll_interval_seconds=0.01,
        now=lambda: NOW,
    )
    viewer = feed.attach(LogFilters())

    viewer.close()
    viewer.close()

    assert viewer.closed
    assert feed.viewer_count == 0


async def test_the_registry_forgets_a_project_no_one_is_watching() -> None:
    registry = tail.TailRegistry()
    repository = _repository(TENANT_A, FakeTailStore([]))

    first = registry.subscribe(
        repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="p"
    )
    second = registry.subscribe(
        repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="p"
    )
    assert registry.live_feeds == 1

    first.close()
    assert registry.live_feeds == 1

    second.close()
    assert registry.live_feeds == 0


async def test_two_tenants_never_share_a_feed() -> None:
    registry = tail.TailRegistry()
    store = FakeTailStore([])

    mine = registry.subscribe(
        _repository(TENANT_A, store),
        tenant_id=TENANT_A,
        project_id=PROJECT,
        filters=LogFilters(),
        person="p",
    )
    theirs = registry.subscribe(
        _repository(TENANT_B, store),
        tenant_id=TENANT_B,
        project_id=PROJECT,
        filters=LogFilters(),
        person="p",
    )

    assert registry.live_feeds == 2
    mine.close()
    theirs.close()
    assert registry.live_feeds == 0


async def test_a_person_may_hold_only_so_many_tails() -> None:
    """A tail is a ClickHouse read a second, and WebSockets pass under the rate
    limiter: this is the only bound on how many one person can start."""
    registry = tail.TailRegistry()
    repository = _repository(TENANT_A, FakeTailStore([]))
    held = [
        registry.subscribe(
            repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="ravi"
        )
        for _ in range(tail.MAX_TAILS_PER_PERSON)
    ]

    with pytest.raises(tail.TailRefusedError, match="Close one"):
        registry.subscribe(
            repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="ravi"
        )

    # A colleague is not held to Ravi's count, and closing one frees a slot.
    registry.subscribe(
        repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="meera"
    ).close()
    held[0].close()
    held[0].close()  # closing twice frees one slot, not two
    held.append(
        registry.subscribe(
            repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="ravi"
        )
    )
    with pytest.raises(tail.TailRefusedError):
        registry.subscribe(
            repository, tenant_id=TENANT_A, project_id=PROJECT, filters=LogFilters(), person="ravi"
        )
    for viewer in held:
        viewer.close()


async def test_an_organization_may_hold_only_so_many_tails() -> None:
    registry = tail.TailRegistry()
    repository = _repository(TENANT_A, FakeTailStore([]))
    held = [
        registry.subscribe(
            repository,
            tenant_id=TENANT_A,
            project_id=PROJECT,
            filters=LogFilters(),
            person=f"person-{n}",
        )
        for n in range(tail.MAX_TAILS_PER_ORGANIZATION)
    ]

    with pytest.raises(tail.TailRefusedError, match="organization"):
        registry.subscribe(
            repository,
            tenant_id=TENANT_A,
            project_id=PROJECT,
            filters=LogFilters(),
            person="someone-new",
        )
    # Another organization's people are not counted against this one.
    registry.subscribe(
        _repository(TENANT_B, FakeTailStore([])),
        tenant_id=TENANT_B,
        project_id=PROJECT,
        filters=LogFilters(),
        person="other",
    ).close()
    for viewer in held:
        viewer.close()


# -------------------------------------------------------------- a failed feed


async def test_a_store_that_keeps_failing_ends_the_tail_rather_than_hanging() -> None:
    class BrokenStore:
        async def query(self, *_: Any, **__: Any) -> FakeQueryResult:
            raise RuntimeError("the store is gone")

    feed = tail.TailFeed(
        TenantScopedRepository(BrokenStore(), tenant_id=TENANT_A),  # type: ignore[arg-type]
        project_id=PROJECT,
        on_idle=lambda: None,
        poll_interval_seconds=0,
        now=lambda: NOW,
    )
    viewer = feed.attach(LogFilters())

    with pytest.raises(tail.TailEndedError):
        await asyncio.wait_for(viewer.next_batch(), timeout=2)
    viewer.close()


# ------------------------------------------------- whose project a tail may open


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_tail_opens_only_on_a_project_the_caller_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this, a made-up project id started a poll loop that read
    ClickHouse every second, matching nothing, for as long as the socket lived."""
    owned = uuid.uuid4()
    org = uuid.uuid4()

    async def find_project(_session: object, *, org_id: uuid.UUID, project_id: uuid.UUID) -> object:
        return object() if (org_id, project_id) == (org, owned) else None

    monkeypatch.setattr(tail, "find_project", find_project)
    resources: Any = SimpleNamespace(session_factory=_Session)
    identity: Any = SimpleNamespace(org_id=org)

    assert await tail._refusal_for(resources, identity, str(owned)) is None
    assert await tail._refusal_for(resources, identity, str(uuid.uuid4())) is not None
    assert await tail._refusal_for(resources, identity, "not-a-uuid") is not None
