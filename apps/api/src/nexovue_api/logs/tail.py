"""The live log tail: new records pushed over a WebSocket as they land.

This is the only persistent connection in the product, so it carries decisions
nothing else needs.

**Tenancy is fixed at the handshake.** The repository arrives through the same
`Depends(get_tenant_repository)` chain every HTTP read uses, so the tenant comes
from the session cookie and from nowhere else. A subscription is a read, and it
obeys the rule in `data/clickhouse/base.py`: nothing a caller sends can name a
tenant. `interpret_command` is the whole vocabulary a client may speak, and it
has two words in it.

**Falling behind is admitted, never hidden.** Each viewer holds a bounded buffer.
When it is full the oldest line is discarded and the count travels with the next
frame, so the screen can say "142 lines dropped" rather than quietly skipping
them. A tail that silently omits lines is worse than one that shows a gap: the
person is watching precisely because they need to trust what they see.

**One poll per project, not per viewer.** Ten tabs on one project share one feed.
The last viewer to leave stops it, so a closed tab releases everything it held.

**Transport.** The feed reads ClickHouse on an interval rather than subscribing
to a broker, and that is a real limitation, not a preference — see the module's
`POLL_INTERVAL_SECONDS` note and the P1-43 report. A stream from the ingest
pipeline is the right long-term source; nothing publishes log records to one today.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from nexovue_api.auth.dependencies import CurrentIdentity, Identity, TenantRepository
from nexovue_api.data.clickhouse import values
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deps import get_resources
from nexovue_api.lifespan import Resources
from nexovue_api.logs.search import LogFilters, LogLine
from nexovue_api.projects.queries import find_project

logger = logging.getLogger(__name__)

# How often a feed asks the store for new arrivals. One second is the shortest
# interval that still reads as "live" to a person; anything faster multiplies the
# scan cost without changing what anyone perceives.
POLL_INTERVAL_SECONDS: Final = 1.0

# What one poll may take. A burst larger than this is not lost — the cursor
# advances only past what was taken, so the remainder arrives on the next turn.
POLL_ROW_LIMIT: Final = 500

# How far back a new tail starts. Ingest is not instantaneous, so a tail that
# started at exactly "now" would show nothing for several seconds and read as
# broken.
PRIMER_WINDOW: Final = timedelta(seconds=10)

# Lines one viewer may hold before the oldest are discarded. Ten seconds of a
# very loud project, which is long enough to survive a pause or a slow socket
# and short enough that the memory of an abandoned tab is bounded.
VIEWER_BUFFER_CAPACITY: Final = 2_000

# Consecutive failed polls before the feed gives up and says so. One transient
# ClickHouse error should not end a tail; a store that is gone should not be
# retried forever in silence.
MAX_CONSECUTIVE_POLL_FAILURES: Final = 5

TAIL_SQL: Final = """
SELECT
    toUnixTimestamp64Nano(timestamp) AS observed_nanos,
    toUnixTimestamp64Nano(received_at) AS arrival_nanos,
    cityHash64(timestamp, service_name, severity_number, body, trace_id, span_id) AS row_key,
    service_name,
    severity,
    severity_number,
    body,
    replaceAll(trace_id, '\\0', '') AS trace_ref,
    replaceAll(span_id, '\\0', '') AS span_ref,
    toJSONString(log_attrs) AS attributes_json
FROM logs
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND received_at >= {since:DateTime64(9)}
ORDER BY received_at
LIMIT {limit:UInt32}
"""


# ------------------------------------------------------- what a client may say


PAUSE: Final = "pause"
RESUME: Final = "resume"
_COMMANDS: Final = frozenset({PAUSE, RESUME})


def interpret_command(message: object) -> str | None:
    """The action this message asks for, or None if it asks for nothing we serve.

    This is the entire vocabulary a subscriber may speak. There is no tenant here
    and no project, by construction: both were settled at the handshake from the
    session, and a message able to change either would be a message able to
    change which tenant's data is read.

    Deliberately total: any shape, any extra field, any unknown action returns
    None rather than raising. A malformed frame must not end someone's tail.
    """
    if not isinstance(message, dict):
        return None
    action = message.get("action")
    if not isinstance(action, str):
        return None
    return action if action in _COMMANDS else None


# ------------------------------------------------------------ what we send back


class TailFrame(BaseModel):
    """A batch of new lines and what was lost getting them here.

    `dropped` is per frame, not cumulative: it counts the lines discarded since
    the previous frame, which is the number the screen has to place in the gap.
    """

    type: str = "records"
    records: list[LogLine] = Field(default_factory=list)
    dropped: int = 0


class TailReady(BaseModel):
    """Sent once, on accept, so the client knows the subscription is live."""

    type: str = "ready"
    project_id: str
    buffer_capacity: int = VIEWER_BUFFER_CAPACITY


class TailFailed(BaseModel):
    """The tail is ending for a reason the person should be told."""

    type: str = "failed"
    detail: str


# ------------------------------------------------------------- the pure pieces


@dataclass(frozen=True, slots=True)
class ArrivedLog:
    """One live line, with our receive clock beside it.

    `arrival_nanos` never reaches the client: it exists so the feed's cursor can
    say what it has already delivered. What the client keys a row on is
    `line.row_key`, the same value search returns.
    """

    line: LogLine
    arrival_nanos: int

    @property
    def row_key(self) -> int:
        return int(self.line.row_key)


def to_arrival(row: dict[str, Any]) -> ArrivedLog:
    return ArrivedLog(
        line=LogLine(
            row_key=str(row["row_key"]),
            observed_at=values.nanos_to_datetime(row["observed_nanos"]),
            service_name=values.text(row["service_name"]),
            severity=values.text(row["severity"]),
            severity_number=int(row["severity_number"]),
            body=values.text(row["body"]),
            trace_id=values.present_trace_id(row["trace_ref"]),
            span_id=values.present_span_id(row["span_ref"]),
            attributes=values.json_object(row["attributes_json"]),
        ),
        arrival_nanos=int(row["arrival_nanos"]),
    )


class TailCursor:
    """How far a feed has read, in our own receive clock.

    A `>` on the last arrival would be wrong: the writer stamps a whole Kafka
    batch with one `received_at`, so records genuinely share a nanosecond, and a
    strict bound would skip every one after the first. The read is therefore
    inclusive and this remembers which rows at that exact instant were already
    delivered — a set bounded by one batch, not by the tail's lifetime.
    """

    def __init__(self, since_nanos: int) -> None:
        self._since_nanos = since_nanos
        self._delivered_at_since: set[int] = set()

    @property
    def since_nanos(self) -> int:
        return self._since_nanos

    def unseen(self, arrivals: Sequence[ArrivedLog]) -> list[ArrivedLog]:
        """The arrivals not yet delivered, advancing past them as a side effect.

        Anything stamped before the cursor is already past, whatever the query
        returned. Enforced here rather than trusted from the statement's bound,
        because a cursor that can go backwards resends a line for as long as the
        tail is open.
        """
        fresh = [
            arrival
            for arrival in arrivals
            if arrival.arrival_nanos >= self._since_nanos
            and not (
                arrival.arrival_nanos == self._since_nanos
                and arrival.row_key in self._delivered_at_since
            )
        ]
        if not fresh:
            return []

        newest = max(arrival.arrival_nanos for arrival in fresh)
        if newest > self._since_nanos:
            self._since_nanos = newest
            self._delivered_at_since = set()
        self._delivered_at_since.update(
            arrival.row_key for arrival in fresh if arrival.arrival_nanos == self._since_nanos
        )
        return fresh


@dataclass(frozen=True, slots=True)
class DrainedLines:
    """What one read of a buffer yields, and what was lost since the last one."""

    records: tuple[ArrivedLog, ...]
    dropped: int


class ViewerBuffer:
    """One viewer's pending lines, bounded, oldest-first out.

    Full means the oldest line goes. The count travels with the next drain rather
    than being logged and forgotten, because the only place a gap can be honestly
    reported is on the screen where the gap is.
    """

    def __init__(self, capacity: int = VIEWER_BUFFER_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("a viewer buffer holding nothing cannot report a gap")
        self._lines: deque[ArrivedLog] = deque(maxlen=capacity)
        self._dropped_since_drain = 0
        self._dropped_total = 0

    @property
    def pending(self) -> int:
        return len(self._lines)

    @property
    def dropped_total(self) -> int:
        return self._dropped_total

    def offer(self, arrivals: Iterable[ArrivedLog]) -> None:
        for arrival in arrivals:
            if len(self._lines) == self._lines.maxlen:
                self._dropped_since_drain += 1
                self._dropped_total += 1
            self._lines.append(arrival)

    def drain(self) -> DrainedLines:
        drained = DrainedLines(records=tuple(self._lines), dropped=self._dropped_since_drain)
        self._lines.clear()
        self._dropped_since_drain = 0
        return drained


def matches(arrival: ArrivedLog, filters: LogFilters) -> bool:
    """Whether one live line belongs on a screen holding these filters.

    Applied per viewer rather than in the query: the feed is shared by everyone
    watching a project, and they do not share filters.
    """
    line = arrival.line
    if filters.service_name and line.service_name != filters.service_name:
        return False
    if filters.min_severity and line.severity_number < filters.min_severity:
        return False
    return not (filters.search and filters.search.lower() not in line.body.lower())


# ------------------------------------------------------- the feed and its viewers


class TailViewer:
    """One subscriber's end of a feed: a bounded buffer and a pause switch."""

    def __init__(
        self,
        filters: LogFilters,
        release: Callable[[TailViewer], None],
        capacity: int = VIEWER_BUFFER_CAPACITY,
    ) -> None:
        self._buffer = ViewerBuffer(capacity)
        self._filters = filters
        self._release = release
        self._ready = asyncio.Event()
        self._paused = False
        self._ended: str | None = None
        self._closed = False

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dropped_total(self) -> int:
        return self._buffer.dropped_total

    def offer(self, arrivals: Sequence[ArrivedLog]) -> None:
        wanted = [arrival for arrival in arrivals if matches(arrival, self._filters)]
        if not wanted:
            return
        self._buffer.offer(wanted)
        self._ready.set()

    def pause(self) -> None:
        """Stop sending. The buffer keeps filling, and keeps dropping the oldest."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._ready.set()

    def end(self, detail: str) -> None:
        """The feed behind this viewer has given up; wake it so it can say so."""
        self._ended = detail
        self._ready.set()

    async def next_batch(self) -> DrainedLines:
        """The next lines to send, once there are some and the viewer wants them.

        Raises `TailEndedError` when the feed behind it failed, so the caller reports
        the failure rather than waiting forever on a dead subscription.
        """
        while True:
            if self._ended is not None:
                raise TailEndedError(self._ended)
            if not self._paused and self._buffer.pending:
                return self._buffer.drain()
            self._ready.clear()
            await self._ready.wait()

    def close(self) -> None:
        """Give up the subscription. Idempotent: a disconnect can arrive twice."""
        if self._closed:
            return
        self._closed = True
        self._release(self)


class TailEndedError(RuntimeError):
    """The feed stopped for a reason the subscriber has to be told."""


FeedKey = tuple[str, str]

# How many tails one person, and one organization, may hold open in this API
# process. A tail is a ClickHouse read a second for as long as it is open, and
# WebSockets pass under the rate limiter, so this is the only bound on how many
# a person can start (docs/changes/clickhouse-protects-itself). Per process, as
# every in-process limit is: several API containers each allow this many.
MAX_TAILS_PER_PERSON: Final = 5
MAX_TAILS_PER_ORGANIZATION: Final = 20


class TailRefusedError(RuntimeError):
    """A tail that may not be opened, with the sentence the subscriber is shown."""


class TailFeed:
    """One poll loop over one project, fanned out to everyone watching it."""

    def __init__(
        self,
        repository: TenantScopedRepository,
        *,
        project_id: str,
        on_idle: Callable[[], None],
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._project_id = project_id
        self._on_idle = on_idle
        self._poll_interval_seconds = poll_interval_seconds
        self._now = now
        self._viewers: list[TailViewer] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def attach(
        self,
        filters: LogFilters,
        capacity: int = VIEWER_BUFFER_CAPACITY,
        on_release: Callable[[], None] | None = None,
    ) -> TailViewer:
        def release(viewer: TailViewer) -> None:
            self._detach(viewer)
            if on_release is not None:
                on_release()

        viewer = TailViewer(filters, release=release, capacity=capacity)
        self._viewers.append(viewer)
        if self._task is None:
            self._task = asyncio.create_task(self._poll_until_idle())
        return viewer

    def _detach(self, viewer: TailViewer) -> None:
        """A viewer has gone. The last one out turns the poll loop off."""
        with contextlib.suppress(ValueError):
            self._viewers.remove(viewer)
        if self._viewers:
            return
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._on_idle()

    async def _poll_until_idle(self) -> None:
        cursor = TailCursor(
            since_nanos=int(
                (self._now() - PRIMER_WINDOW).timestamp() * values.NANOSECONDS_PER_SECOND
            )
        )
        failures = 0
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                arrivals = await self._read_since(cursor.since_nanos)
            except Exception:
                failures += 1
                logger.warning(
                    "log tail poll failed",
                    extra={"project_id": self._project_id, "consecutive_failures": failures},
                    exc_info=True,
                )
                if failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    self._end_every_viewer("The live tail lost its connection to storage.")
                    return
                continue

            failures = 0
            fresh = cursor.unseen(arrivals)
            if not fresh:
                continue
            for viewer in list(self._viewers):
                viewer.offer(fresh)

    async def _read_since(self, since_nanos: int) -> list[ArrivedLog]:
        rows = await self._repository.query(
            TAIL_SQL,
            {
                "project_id": self._project_id,
                "since": values.nanos_to_datetime(since_nanos),
                "limit": POLL_ROW_LIMIT,
            },
        )
        return [to_arrival(row) for row in rows]

    def _end_every_viewer(self, detail: str) -> None:
        for viewer in list(self._viewers):
            viewer.end(detail)


@dataclass
class TailRegistry:
    """The live feeds of this process, one per tenant and project.

    Keyed by tenant as well as project so two organizations can never share a
    feed, whatever their project ids happen to be.
    """

    _feeds: dict[FeedKey, TailFeed] = field(default_factory=dict)
    _held_by_person: Counter[str] = field(default_factory=Counter)
    _held_by_organization: Counter[str] = field(default_factory=Counter)

    @property
    def live_feeds(self) -> int:
        return len(self._feeds)

    def subscribe(
        self,
        repository: TenantScopedRepository,
        *,
        tenant_id: str,
        project_id: str,
        filters: LogFilters,
        person: str,
        capacity: int = VIEWER_BUFFER_CAPACITY,
    ) -> TailViewer:
        if self._held_by_person[person] >= MAX_TAILS_PER_PERSON:
            raise TailRefusedError(
                f"You already have {MAX_TAILS_PER_PERSON} live tails open. "
                "Close one to open another."
            )
        if self._held_by_organization[tenant_id] >= MAX_TAILS_PER_ORGANIZATION:
            raise TailRefusedError(
                f"Your organization already has {MAX_TAILS_PER_ORGANIZATION} live tails open. "
                "Close one to open another."
            )
        key = (tenant_id, project_id)
        feed = self._feeds.get(key)
        if feed is None:
            feed = TailFeed(repository, project_id=project_id, on_idle=self._forget(key))
            self._feeds[key] = feed
        self._held_by_person[person] += 1
        self._held_by_organization[tenant_id] += 1

        def release_slot() -> None:
            self._held_by_person[person] -= 1
            self._held_by_organization[tenant_id] -= 1

        return feed.attach(filters, capacity=capacity, on_release=release_slot)

    def _forget(self, key: FeedKey) -> Callable[[], None]:
        """What a feed calls once nobody is watching it any more."""

        def drop() -> None:
            self._feeds.pop(key, None)

        return drop


registry: Final = TailRegistry()


# ------------------------------------------------------------------ the route

router = APIRouter()


@router.websocket("/tail")
async def tail(
    websocket: WebSocket,
    identity: CurrentIdentity,
    repository: TenantRepository,
    resources: Annotated[Resources, Depends(get_resources)],
    project_id: Annotated[str, Query(description="Project to tail")],
    q: Annotated[str | None, Query(description="Case-insensitive substring of the body")] = None,
    service: Annotated[str | None, Query(description="Exactly one service name")] = None,
    min_severity: Annotated[int | None, Query(description="OTLP severity number floor")] = None,
) -> None:
    """Push every new log record for one project until the client goes away.

    `identity` and `repository` come from the session cookie through the same
    dependency chain every HTTP read uses; neither the query string nor any later
    message can name a tenant.

    The project is checked against Postgres before the tail starts, with a session
    opened and closed for that alone — a dependency would hold a pooled connection
    for the whole life of the tail. The repository's tenant binding already made a
    stranger's project match no rows; the check is what stops a made-up project
    id from starting a poll loop that reads ClickHouse every second for nothing.
    """
    refusal = await _refusal_for(resources, identity, project_id)
    filters = LogFilters(service_name=service, min_severity=min_severity, search=q)
    viewer = None
    if refusal is None:
        try:
            viewer = registry.subscribe(
                repository,
                tenant_id=str(identity.org_id),
                project_id=project_id,
                filters=filters,
                person=str(identity.user.id),
            )
        except TailRefusedError as refused:
            refusal = str(refused)

    await websocket.accept()
    if viewer is None:
        # Accepted first so the reason reaches the screen: a socket refused at the
        # handshake can say nothing but 403.
        with contextlib.suppress(Exception):
            await websocket.send_text(TailFailed(detail=refusal or "").model_dump_json())
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        await websocket.send_text(TailReady(project_id=project_id).model_dump_json())
        async with asyncio.TaskGroup() as connection:
            connection.create_task(_obey_client(websocket, viewer))
            connection.create_task(_push_lines(websocket, viewer))
    except* WebSocketDisconnect:
        pass  # The tab closed. The `finally` below is the whole cleanup.
    except* TailEndedError as ended:
        await _report_failure(websocket, ended.exceptions)
    finally:
        viewer.close()


async def _refusal_for(resources: Resources, identity: Identity, project_id: str) -> str | None:
    """Why this tail may not open, or None when it may."""
    try:
        project = uuid.UUID(project_id)
    except ValueError:
        return "That is not a project."
    async with resources.session_factory() as session:
        owned = await find_project(session, org_id=identity.org_id, project_id=project)
    return None if owned is not None else "That is not a project."


async def _obey_client(websocket: WebSocket, viewer: TailViewer) -> None:
    """Read the two words a subscriber may say. Everything else is ignored."""
    while True:
        action = interpret_command(await websocket.receive_json())
        if action == PAUSE:
            viewer.pause()
        elif action == RESUME:
            viewer.resume()


async def _push_lines(websocket: WebSocket, viewer: TailViewer) -> None:
    """Send batches as they accumulate, each carrying what was dropped before it."""
    while True:
        batch = await viewer.next_batch()
        await websocket.send_text(
            TailFrame(
                records=[arrival.line for arrival in batch.records],
                dropped=batch.dropped,
            ).model_dump_json()
        )


async def _report_failure(websocket: WebSocket, failures: Sequence[BaseException]) -> None:
    """Say why the tail ended before the socket closes, if it still can."""
    detail = str(failures[0]) if failures else "The live tail ended."
    with contextlib.suppress(Exception):
        await websocket.send_text(TailFailed(detail=detail).model_dump_json())
