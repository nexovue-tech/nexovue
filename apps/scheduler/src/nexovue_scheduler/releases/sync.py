"""The release sync: from the version rollup to `releases` and `release_periods` (F28
design 3, *From telemetry*; plan T1.10).

- **`sweep:releases`**, on beat's minute, hands each active project whose services have
  sent anything since its last run one `observe_project` job, one organization's
  project in turn (`fair_order`), on the capped `releases` queue, each expiring within
  the minute so a later sweep replaces a late one.
- **`observe_project`** holds the project's session lock, reads the rollup by minute
  and version up to the settled minute, walks each service's minutes in order, and
  writes what changed in one transaction, sorted by service and version, the order the
  deploy webhook will take its row locks in (design 3, step 5; AC51).

**The walk** (FR19, FR42, FR43; all thresholds starting guesses). A version's first
counted minute sets its first-seen time and opens its period, flagged already running
when it is the first version counted on the service with no report before it. A period
ends when another version holds over 90% of the service's named requests for 10
minutes in a row, each with traffic, dated from the run's first minute; or when its
version has had no requests for 60 minutes while others had at least 20, dated the
minute after its last request. An ended version comes back with at least 20% of them
and 5 requests over 10 minutes, from that stretch's first minute with its traffic. A
period's start, once counted, never moves; a reported one takes its first counted
minute (FR42). No version and the worker's fold are never read (`store`).

**How far it reads** (design 3, step 10). `synced_through` is the last minute read, and
moves only forward, to the lesser of the settled minute and the newest minute read. A
run decides at most 6 hours of new minutes, and reads the hour before them again: the
60-minute rule needs it, and a version first counted in rows that arrived late within
that hour still gets its true first minute (AC55). A version seen *only* in rows later
than that is missed until counted again, the design's stated risk, over an hour now.

**Bounds.** At most 200 new releases a run; past it the run stops at the last minute
finished, and the next carries on (step 9). A project over 50,000 releases evicts
before creating: first never-seen releases of services it has never heard from, then
the least recently active with no period in service, which is never evicted (FR56).
A row whose names Postgres cannot hold is skipped and counted (FR18, AC56).

Every statement binds `org_id`, read from the project's own row (F6's m6), except the
one named in `test_scheduler_postgres_tenancy_structure.py` that finds whose services
have moved since their last run, across every organization.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.detection.projects import active_projects, fair_order, session_lock
from nexovue_scheduler.detection.store import ProjectBinding, bind_project
from nexovue_scheduler.jobs.queue import enqueue_all
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.releases import OBSERVE_PROJECT_TASK, RELEASES_QUEUE, SWEEP_RELEASES_TASK
from nexovue_scheduler.releases.store import ReleaseStore, VersionCount, connect_store
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# ------------------------------------------------------------------ the bounds

MINUTE: Final = timedelta(minutes=1)
# New minutes one run decides (a guess; design 3, step 4).
RUN_BOUND: Final = timedelta(hours=6)
# What a later run reads again before its new minutes: the 60-minute rule's reach.
REREAD: Final = timedelta(minutes=60)
NEW_RELEASES_PER_RUN: Final = 200
RELEASES_PER_PROJECT: Final = releases.RELEASES_PER_PROJECT
# A job not started within a minute is overtaken by the next sweep's.
JOB_EXPIRES_SECONDS: Final = 60
SOFT_LIMIT_SECONDS: Final = 40
HARD_LIMIT_SECONDS: Final = 55
# The claim takes the oldest `run_after` first; one transaction's jobs share its `now()`.
ORDER_STEP_SECONDS: Final = 0.001
_LOCK: Final = "release-sync"

# ------------------------------------------------------------ the period rules

# Another version over this share of named requests, this many minutes in a row, ends a
# period (FR19).
TAKEN_OVER_SHARE: Final = 0.90
TAKEN_OVER_MINUTES: Final = 10
# No requests for this long, while others had this many, ends a period.
QUIET_FOR: Final = timedelta(minutes=60)
OTHERS_WHILE_QUIET: Final = 20
# An ended version returns with this share and this many requests over this stretch.
RETURN_SHARE: Final = 0.20
RETURN_REQUESTS: Final = 5
RETURN_MINUTES: Final = 10
# A new period's previous version is the busiest other one this long before it (FR20).
PREVIOUS_LOOKBACK: Final = timedelta(hours=1)
# The minutes of history the rules above look back over.
_HISTORY_MINUTES: Final = int(PREVIOUS_LOOKBACK / MINUTE) + RETURN_MINUTES

# --------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class KnownRelease:
    """A release Postgres holds, as the walk needs it."""

    service_name: str
    version: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    first_reported_at: datetime | None


@dataclass(frozen=True, slots=True)
class KnownPeriod:
    """A release's latest period, as Postgres holds it."""

    id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime
    ended_at: datetime | None
    start_basis: str


@dataclass(frozen=True, slots=True)
class SyncWindow:
    """What one run reads, `[read_from, read_to)`, and decides, from `decide_from`."""

    read_from: datetime
    decide_from: datetime
    read_to: datetime


def sync_window(synced_through: datetime | None, settled: datetime) -> SyncWindow | None:
    """The minutes this run reads, or None when nothing has settled since the last."""
    read_to = settled + MINUTE
    if synced_through is None:
        start = read_to - RUN_BOUND
        return SyncWindow(read_from=start, decide_from=start, read_to=read_to)
    decide_from = synced_through + MINUTE
    if read_to <= decide_from:
        return None
    return SyncWindow(
        read_from=decide_from - REREAD,
        decide_from=decide_from,
        read_to=min(read_to, decide_from + RUN_BOUND),
    )


def storable_counts(rows: Iterable[VersionCount]) -> tuple[list[VersionCount], int]:
    """The rows that can become releases, and how many could not (FR18)."""
    every = list(rows)
    kept = [row for row in every if releases.storable(row.service_name, row.version)]
    return kept, len(every) - len(kept)


# -------------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True)
class NewPeriod:
    service_name: str
    version: str
    started_at: datetime
    ended_at: datetime | None
    previous_version: str | None
    already_running: bool
    is_return: bool


@dataclass(frozen=True, slots=True)
class PeriodEnd:
    period_id: uuid.UUID
    service_name: str
    version: str
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class CountedStart:
    """A reported period's first counted minute, which becomes its start (FR42)."""

    period_id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Changes:
    # (service, version) to the first and last minute counted in this run.
    seen: Mapping[tuple[str, str], tuple[datetime, datetime]]
    new_periods: tuple[NewPeriod, ...]
    ends: tuple[PeriodEnd, ...]
    counted_starts: tuple[CountedStart, ...]
    synced_through: datetime
    new_releases: int
    capped: bool

    def releases_written(self) -> list[tuple[str, str]]:
        """Every release this run writes, in the order its row locks are taken."""
        named = set(self.seen)
        named |= {(p.service_name, p.version) for p in self.new_periods}
        named |= {(e.service_name, e.version) for e in self.ends}
        named |= {(c.service_name, c.version) for c in self.counted_starts}
        return sorted(named)


# ------------------------------------------------------------------- the walk


@dataclass
class _Open:
    started_at: datetime
    # A period Postgres holds; None for one opened in this run.
    period_id: uuid.UUID | None
    start_basis: str
    # This run's new period, while open.
    opened: _Opened | None = None


@dataclass
class _Opened:
    service_name: str
    version: str
    started_at: datetime
    previous_version: str | None
    already_running: bool
    is_return: bool
    ended_at: datetime | None = None


@dataclass
class _Run:
    """A version holding over `TAKEN_OVER_SHARE` in consecutive minutes with traffic."""

    version: str
    first: datetime
    last: datetime


@dataclass
class _Service:
    """One service's state through the walk, from what Postgres holds."""

    name: str
    read_from: datetime
    first_seen: dict[str, datetime | None]
    first_reported: dict[str, datetime | None]
    last_request: dict[str, datetime]
    open: dict[str, _Open]
    ended: dict[str, datetime]
    # (start, version) of every period known or opened, for the previous version's fallback.
    starts: list[tuple[datetime, str]]
    history: deque[tuple[datetime, Mapping[str, int]]] = field(
        default_factory=lambda: deque(maxlen=_HISTORY_MINUTES + 1)
    )
    run: _Run | None = None
    seen: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    opened: list[_Opened] = field(default_factory=list)
    ends: list[PeriodEnd] = field(default_factory=list)
    counted_starts: list[CountedStart] = field(default_factory=list)

    def knows(self, version: str) -> bool:
        return self.first_seen.get(version) is not None

    def step(self, minute: datetime, counts: Mapping[str, int], *, decide: bool) -> None:
        self.history.append((minute, counts))
        self._follow_run(minute, counts)
        for version in sorted(counts):
            self._count(minute, version)
        if not decide:
            return
        for version in sorted(self.open):
            ended = self._end_of(version, self.open[version], minute)
            if ended is not None:
                self._close(version, ended)
        for version in sorted(self.ended):
            if version not in self.open:
                self._maybe_return(version, minute)

    def _count(self, minute: datetime, version: str) -> None:
        first, last = self.seen.get(version, (minute, minute))
        self.seen[version] = (min(first, minute), max(last, minute))
        self.last_request[version] = max(self.last_request.get(version, minute), minute)
        if self.knows(version):
            return
        self.first_seen[version] = minute
        held = self.open.get(version)
        if (
            held is not None
            and held.period_id is not None
            and held.start_basis == releases.REPORTED
        ):
            self.counted_starts.append(CountedStart(held.period_id, self.name, version, minute))
            held.started_at, held.start_basis = minute, releases.SEEN
        elif held is None and version not in self.ended:
            self._open(version, minute, is_return=False)

    def _open(self, version: str, started_at: datetime, *, is_return: bool) -> None:
        opened = _Opened(
            service_name=self.name,
            version=version,
            started_at=started_at,
            previous_version=self._previous(version, started_at),
            already_running=not is_return and self._first_on_service(version, started_at),
            is_return=is_return,
        )
        self.opened.append(opened)
        self.open[version] = _Open(started_at, None, releases.SEEN, opened)
        self.starts.append((started_at, version))
        self.ended.pop(version, None)

    def _close(self, version: str, ended_at: datetime) -> None:
        held = self.open.pop(version)
        ended_at = max(ended_at, held.started_at)
        if held.opened is not None:
            held.opened.ended_at = ended_at
        elif held.period_id is not None:
            self.ends.append(PeriodEnd(held.period_id, self.name, version, ended_at))
        self.ended[version] = ended_at

    def _follow_run(self, minute: datetime, counts: Mapping[str, int]) -> None:
        total = sum(counts.values())
        if total <= 0:
            self.run = None
            return
        top = min(counts, key=lambda version: (-counts[version], version))
        if counts[top] / total <= TAKEN_OVER_SHARE:
            self.run = None
        elif self.run is not None and self.run.version == top and self.run.last == minute - MINUTE:
            self.run.last = minute
        else:
            self.run = _Run(top, minute, minute)

    def _end_of(self, version: str, held: _Open, minute: datetime) -> datetime | None:
        run = self.run
        if run is not None and run.version != version and run.last == minute:
            since = max(run.first, held.started_at)
            if minute - since + MINUTE >= TAKEN_OVER_MINUTES * MINUTE:
                return since
        quiet_from = minute - QUIET_FOR + MINUTE
        if quiet_from < self.read_from:
            return None
        last = self.last_request.get(version)
        if last is not None and last >= quiet_from:
            return None
        others = sum(
            requests
            for at, counts in self.history
            if at >= quiet_from
            for other, requests in counts.items()
            if other != version
        )
        if others < OTHERS_WHILE_QUIET:
            return None
        return last + MINUTE if last is not None else quiet_from

    def _maybe_return(self, version: str, minute: datetime) -> None:
        stretch_from = minute - (RETURN_MINUTES - 1) * MINUTE
        if stretch_from < self.read_from or stretch_from < self.ended[version]:
            return
        stretch = [(at, counts) for at, counts in self.history if at >= stretch_from]
        own = sum(counts.get(version, 0) for _, counts in stretch)
        total = sum(sum(counts.values()) for _, counts in stretch)
        if own < RETURN_REQUESTS or own < RETURN_SHARE * total:
            return
        first = next(at for at, counts in stretch if counts.get(version, 0) > 0)
        self._open(version, first, is_return=True)

    def _previous(self, version: str, started_at: datetime) -> str | None:
        totals: dict[str, int] = defaultdict(int)
        for at, counts in self.history:
            if started_at - PREVIOUS_LOOKBACK <= at < started_at:
                for other, requests in counts.items():
                    if other != version:
                        totals[other] += requests
        if totals:
            return min(totals, key=lambda other: (-totals[other], other))
        earlier = [(at, other) for at, other in self.starts if at < started_at and other != version]
        return max(earlier)[1] if earlier else None

    def _first_on_service(self, version: str, started_at: datetime) -> bool:
        reported = self.first_reported.get(version)
        if reported is not None and reported <= started_at:
            return False
        return not any(
            seen is not None and seen < started_at
            for other, seen in self.first_seen.items()
            if other != version
        )


def walk(
    counts: Sequence[VersionCount],
    known_releases: Sequence[KnownRelease],
    known_periods: Sequence[KnownPeriod],
    window: SyncWindow,
    *,
    new_release_cap: int = NEW_RELEASES_PER_RUN,
) -> Changes:
    """Every service's minutes in order, from what Postgres holds to what changed."""
    grid: dict[str, dict[datetime, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for row in counts:
        if window.read_from <= row.minute < window.read_to and row.requests > 0:
            grid[row.service_name][row.minute][row.version] = row.requests
    services = {name: _service(name, window, known_releases, known_periods) for name in grid}
    new_releases, capped = 0, False
    synced_through = window.read_to - MINUTE
    minute = window.read_from
    while minute < window.read_to:
        newcomers = sum(
            1
            for name, state in services.items()
            for version in grid[name].get(minute, {})
            if not state.knows(version)
        )
        # Never stop before anything new is taken, or a busy minute would stall the sync.
        if newcomers and new_releases and new_releases + newcomers > new_release_cap:
            synced_through, capped = minute - MINUTE, True
            break
        new_releases += newcomers
        for name in sorted(services):
            services[name].step(
                minute, grid[name].get(minute, {}), decide=minute >= window.decide_from
            )
        minute += MINUTE
    return _changes(services.values(), synced_through, new_releases, capped)


def _service(
    name: str,
    window: SyncWindow,
    known_releases: Sequence[KnownRelease],
    known_periods: Sequence[KnownPeriod],
) -> _Service:
    own = [release for release in known_releases if release.service_name == name]
    periods = [period for period in known_periods if period.service_name == name]
    return _Service(
        name=name,
        read_from=window.read_from,
        first_seen={release.version: release.first_seen_at for release in own},
        first_reported={release.version: release.first_reported_at for release in own},
        last_request={
            release.version: release.last_seen_at for release in own if release.last_seen_at
        },
        open={
            period.version: _Open(period.started_at, period.id, period.start_basis)
            for period in periods
            if period.ended_at is None
        },
        ended={
            period.version: period.ended_at for period in periods if period.ended_at is not None
        },
        starts=[(period.started_at, period.version) for period in periods],
    )


def _changes(
    services: Iterable[_Service], synced_through: datetime, new_releases: int, capped: bool
) -> Changes:
    states = sorted(services, key=lambda state: state.name)
    return Changes(
        seen={
            (state.name, version): span for state in states for version, span in state.seen.items()
        },
        new_periods=tuple(
            sorted(
                (
                    NewPeriod(
                        service_name=opened.service_name,
                        version=opened.version,
                        started_at=opened.started_at,
                        ended_at=opened.ended_at,
                        previous_version=opened.previous_version,
                        already_running=opened.already_running,
                        is_return=opened.is_return,
                    )
                    for state in states
                    for opened in state.opened
                ),
                key=lambda period: (period.service_name, period.version, period.started_at),
            )
        ),
        ends=tuple(end for state in states for end in state.ends),
        counted_starts=tuple(start for state in states for start in state.counted_starts),
        synced_through=synced_through,
        new_releases=new_releases,
        capped=capped,
    )


# ---------------------------------------------------------- handing out work

# Across every organization on purpose: the projects whose services have sent anything
# since their last sync, or that were never synced. The usage meter's `service_last_seen`
# answers from Postgres, so an idle project costs no ClickHouse read.
_MOVED_SINCE_SYNC: Final = text(
    """
    SELECT seen.org_id, seen.project_id
    FROM service_last_seen AS seen
    LEFT JOIN release_sync_state AS synced
      ON synced.org_id = seen.org_id AND synced.project_id = seen.project_id
    GROUP BY seen.org_id, seen.project_id, synced.synced_through
    HAVING synced.synced_through IS NULL OR max(seen.last_seen_at) > synced.synced_through
    """
)


def observe_request(org_id: uuid.UUID, project_id: uuid.UUID, position: int = 0) -> JobRequest:
    """One project's sync job: ids only, since job arguments are stored readably."""
    return JobRequest(
        task=OBSERVE_PROJECT_TASK,
        queue=RELEASES_QUEUE,
        kwargs={"project_id": str(project_id)},
        org_id=str(org_id),
        countdown_seconds=position * ORDER_STEP_SECONDS,
        expires_seconds=JOB_EXPIRES_SECONDS,
        soft_limit_seconds=SOFT_LIMIT_SECONDS,
        hard_limit_seconds=HARD_LIMIT_SECONDS,
    )


async def queue_observations(engine: AsyncEngine, *, now: datetime) -> int:
    """Hand each active project that has moved since its last sync one job, one
    organization's project in turn. Returns how many were queued."""
    async with engine.connect() as connection:
        active = fair_order(await active_projects(connection, now=now))
        moved = {
            (row.org_id, row.project_id) for row in await connection.execute(_MOVED_SINCE_SYNC)
        }
    chosen = [project for project in active if (project.org_id, project.project_id) in moved]
    await enqueue_all(
        engine,
        [
            observe_request(project.org_id, project.project_id, position)
            for position, project in enumerate(chosen)
        ],
    )
    return len(chosen)


# ------------------------------------------------------------- one project

_SETTLED: Final = text("SELECT settled_minute FROM detection_sweep")
_SYNCED_THROUGH: Final = text(
    """
    SELECT synced_through FROM release_sync_state
    WHERE org_id = :org_id AND project_id = :project_id
    """
)
_KNOWN_RELEASES: Final = text(
    """
    SELECT service_name, version, first_seen_at, last_seen_at, first_reported_at
    FROM releases
    WHERE org_id = :org_id AND project_id = :project_id AND service_name = ANY(:services)
    """
)
# Each release's latest period: whether it is in service, and since when.
_LATEST_PERIODS: Final = text(
    """
    SELECT DISTINCT ON (periods.release_id)
           periods.id, periods.service_name, known.version, periods.started_at,
           periods.ended_at, periods.start_basis
    FROM release_periods AS periods
    JOIN releases AS known ON known.org_id = periods.org_id AND known.id = periods.release_id
    WHERE periods.org_id = :org_id AND periods.project_id = :project_id
      AND periods.service_name = ANY(:services)
    ORDER BY periods.release_id, periods.started_at DESC
    """
)
_RELEASE_COUNT: Final = text(
    "SELECT count(*) FROM releases WHERE org_id = :org_id AND project_id = :project_id"
)
# FR56's order: never-seen releases of services the project has never heard from, then
# the least recently active; never one with a period in service, nor one this run writes.
_EVICT: Final = text(
    """
    DELETE FROM releases
    WHERE org_id = :org_id AND id IN (
        SELECT candidate.id
        FROM releases AS candidate
        WHERE candidate.org_id = :org_id AND candidate.project_id = :project_id
          AND NOT (candidate.service_name, candidate.version) IN (
              SELECT * FROM unnest(CAST(:services AS text[]), CAST(:versions AS text[])))
          AND NOT EXISTS (
              SELECT 1 FROM release_periods AS serving
              WHERE serving.org_id = :org_id AND serving.release_id = candidate.id
                AND serving.ended_at IS NULL)
        ORDER BY (candidate.first_seen_at IS NULL AND NOT EXISTS (
                      SELECT 1 FROM service_last_seen AS heard
                      WHERE heard.org_id = :org_id AND heard.project_id = :project_id
                        AND heard.service_name = candidate.service_name)) DESC,
                 candidate.last_activity_at, candidate.id
        LIMIT :excess
    )
    """
)
# LEAST and GREATEST ignore a null, so a reported-only release takes its first count.
# The latest arrival is the sync's clock, never a minute's (D20: retention reads it).
_SAVE_RELEASE: Final = text(
    """
    INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at,
                          last_seen_at, last_activity_at)
    VALUES (:org_id, :project_id, :service, :version, :first_seen, :last_seen, now())
    ON CONFLICT (project_id, service_name, version) DO UPDATE
    SET first_seen_at = LEAST(releases.first_seen_at, EXCLUDED.first_seen_at),
        last_seen_at = GREATEST(releases.last_seen_at, EXCLUDED.last_seen_at),
        last_activity_at = GREATEST(releases.last_activity_at, EXCLUDED.last_activity_at)
    WHERE releases.org_id = :org_id
    RETURNING id
    """
)
_RELEASE_ID: Final = text(
    """
    SELECT id FROM releases
    WHERE org_id = :org_id AND project_id = :project_id
      AND service_name = :service AND version = :version
    """
)
# A retried run, or a second period at one start, writes nothing twice.
_OPEN_PERIOD: Final = text(
    """
    INSERT INTO release_periods (org_id, release_id, project_id, service_name, started_at,
                                 start_basis, ended_at, previous_version, already_running,
                                 is_return)
    VALUES (:org_id, :release_id, :project_id, :service, :started_at, 'seen', :ended_at,
            :previous_version, :already_running, :is_return)
    ON CONFLICT DO NOTHING
    """
)
# A past period only ever gains an end (FR19).
_END_PERIOD: Final = text(
    """
    UPDATE release_periods SET ended_at = :ended_at
    WHERE org_id = :org_id AND id = :id AND ended_at IS NULL AND started_at <= :ended_at
    """
)
# A reported start gives way to the first counted minute, once (FR42).
_COUNT_START: Final = text(
    """
    UPDATE release_periods SET started_at = :started_at, start_basis = 'seen'
    WHERE org_id = :org_id AND id = :id AND start_basis = 'reported'
    """
)
_SAVE_SYNCED_THROUGH: Final = text(
    """
    INSERT INTO release_sync_state (org_id, project_id, synced_through)
    VALUES (:org_id, :project_id, :synced_through)
    ON CONFLICT (project_id) DO UPDATE
    SET synced_through = GREATEST(release_sync_state.synced_through, EXCLUDED.synced_through)
    WHERE release_sync_state.org_id = :org_id
    """
)


@dataclass(frozen=True, slots=True)
class Observed:
    """What one run did, for its log line and the tests."""

    rows_read: int
    rows_skipped: int
    new_releases: int
    new_periods: int
    ended_periods: int
    evicted: int
    synced_through: datetime | None
    capped: bool


NOTHING_OBSERVED: Final = Observed(0, 0, 0, 0, 0, 0, None, False)


async def observe_project(
    engine: AsyncEngine,
    store: ReleaseStore,
    project_id: uuid.UUID,
    *,
    new_release_cap: int = NEW_RELEASES_PER_RUN,
    release_cap: int = RELEASES_PER_PROJECT,
) -> Observed:
    """One project's sync: read, walk and write, under the project's session lock."""
    async with engine.connect() as connection:
        binding = await bind_project(connection, project_id)
        if binding is None:
            return NOTHING_OBSERVED
        async with session_lock(connection, _LOCK, project_id) as held:
            if not held:
                return NOTHING_OBSERVED
            observed = await _observe(
                connection,
                binding,
                store,
                new_release_cap=new_release_cap,
                release_cap=release_cap,
            )
            await connection.commit()
    if observed.rows_skipped or observed.evicted or observed.capped:
        logger.warning(
            "release_sync_bounded",
            org_id=binding.org_id,
            project_id=binding.project_id,
            rows_skipped=observed.rows_skipped,
            evicted=observed.evicted,
            capped=observed.capped,
        )
    return observed


async def _observe(
    connection: AsyncConnection,
    binding: ProjectBinding,
    store: ReleaseStore,
    *,
    new_release_cap: int,
    release_cap: int,
) -> Observed:
    keys = _keys(binding)
    settled = await connection.scalar(_SETTLED)
    if settled is None:
        return NOTHING_OBSERVED
    window = sync_window(await connection.scalar(_SYNCED_THROUGH, keys), settled)
    if window is None:
        return NOTHING_OBSERVED
    read = store.for_project(binding.org_id, binding.project_id).version_minutes(
        start=window.read_from, end=window.read_to
    )
    kept, skipped = storable_counts(read)
    services = sorted({row.service_name for row in kept})
    known_releases, known_periods = await _known(connection, keys, services)
    changes = walk(kept, known_releases, known_periods, window, new_release_cap=new_release_cap)
    evicted = await _make_room(connection, keys, changes, release_cap)
    await _write(connection, binding, changes)
    return Observed(
        rows_read=len(read),
        rows_skipped=skipped,
        new_releases=changes.new_releases,
        new_periods=len(changes.new_periods),
        ended_periods=len(changes.ends) + sum(1 for p in changes.new_periods if p.ended_at),
        evicted=evicted,
        synced_through=changes.synced_through,
        capped=changes.capped,
    )


async def _known(
    connection: AsyncConnection, keys: Mapping[str, Any], services: Sequence[str]
) -> tuple[list[KnownRelease], list[KnownPeriod]]:
    if not services:
        return [], []
    scoped = {**keys, "services": list(services)}
    known_releases = [
        KnownRelease(
            service_name=row.service_name,
            version=row.version,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            first_reported_at=row.first_reported_at,
        )
        for row in await connection.execute(_KNOWN_RELEASES, scoped)
    ]
    known_periods = [
        KnownPeriod(
            id=row.id,
            service_name=row.service_name,
            version=row.version,
            started_at=row.started_at,
            ended_at=row.ended_at,
            start_basis=row.start_basis,
        )
        for row in await connection.execute(_LATEST_PERIODS, scoped)
    ]
    return known_releases, known_periods


async def _make_room(
    connection: AsyncConnection, keys: Mapping[str, Any], changes: Changes, release_cap: int
) -> int:
    """Evict before creating, so a new real version always appears (FR56, AC60)."""
    if not changes.new_releases:
        return 0
    held = int(await connection.scalar(_RELEASE_COUNT, keys) or 0)
    excess = held + changes.new_releases - release_cap
    if excess <= 0:
        return 0
    written = changes.releases_written()
    result = await connection.execute(
        _EVICT,
        {
            **keys,
            "services": [service for service, _ in written],
            "versions": [version for _, version in written],
            "excess": excess,
        },
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def _write(connection: AsyncConnection, binding: ProjectBinding, changes: Changes) -> None:
    """Everything the walk decided, sorted by service and version (AC51)."""
    keys = _keys(binding)
    periods = _by_key(changes.new_periods)
    ends = _by_key(changes.ends)
    starts = _by_key(changes.counted_starts)
    for service, version in changes.releases_written():
        named = {**keys, "service": service, "version": version}
        release_id = await _release_id(connection, named, changes.seen.get((service, version)))
        for start in starts.get((service, version), ()):
            await connection.execute(
                _COUNT_START, {**keys, "id": start.period_id, "started_at": start.started_at}
            )
        for end in ends.get((service, version), ()):
            await connection.execute(
                _END_PERIOD, {**keys, "id": end.period_id, "ended_at": end.ended_at}
            )
        for period in periods.get((service, version), ()):
            await connection.execute(
                _OPEN_PERIOD,
                {
                    **named,
                    "release_id": release_id,
                    "started_at": period.started_at,
                    "ended_at": period.ended_at,
                    "previous_version": period.previous_version,
                    "already_running": period.already_running,
                    "is_return": period.is_return,
                },
            )
    await connection.execute(
        _SAVE_SYNCED_THROUGH, {**keys, "synced_through": changes.synced_through}
    )


async def _release_id(
    connection: AsyncConnection,
    named: Mapping[str, Any],
    seen: tuple[datetime, datetime] | None,
) -> uuid.UUID:
    if seen is None:
        found = await connection.scalar(_RELEASE_ID, named)
    else:
        found = await connection.scalar(
            _SAVE_RELEASE, {**named, "first_seen": seen[0], "last_seen": seen[1]}
        )
    if not isinstance(found, uuid.UUID):
        raise LookupError(f"no release {named['service']!r} {named['version']!r} to write to")
    return found


def _by_key[Item: (NewPeriod, PeriodEnd, CountedStart)](
    items: Iterable[Item],
) -> dict[tuple[str, str], list[Item]]:
    grouped: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for item in items:
        grouped[(item.service_name, item.version)].append(item)
    return grouped


def _keys(binding: ProjectBinding) -> dict[str, uuid.UUID]:
    return {"org_id": uuid.UUID(binding.org_id), "project_id": uuid.UUID(binding.project_id)}


# --------------------------------------------------------------------- tasks

_store: ReleaseStore | None = None

NO_STORE: Final = "CLICKHOUSE_DSN is not set for the scheduler; releases are not synced"


def _the_store() -> ReleaseStore | None:
    global _store
    if _store is None:
        dsn = get_settings().clickhouse_dsn
        if not dsn:
            logger.error(NO_STORE)
            return None
        _store = connect_store(dsn)
    return _store


@task(SWEEP_RELEASES_TASK)
def sweep_releases_task() -> None:
    now = datetime.now(UTC)
    queued = with_engine(lambda engine: queue_observations(engine, now=now))
    logger.info("release_sweep", projects=queued)


@task(OBSERVE_PROJECT_TASK)
def observe_project_task(project_id: str) -> None:
    store = _the_store()
    if store is None:
        return
    started = time.monotonic()
    observed = with_engine(lambda engine: observe_project(engine, store, uuid.UUID(project_id)))
    logger.info(
        "release_sync",
        project_id=project_id,
        duration_ms=round((time.monotonic() - started) * 1000),
        rows_read=observed.rows_read,
        new_releases=observed.new_releases,
        new_periods=observed.new_periods,
        ended_periods=observed.ended_periods,
    )
