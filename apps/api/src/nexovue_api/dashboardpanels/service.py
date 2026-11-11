"""Every panel of a dashboard in one request, saved or a draft (F20, design section 5).

The sequence, and why each step is where it is:

1. **Postgres first, all of it.** The dashboard, the project, each check a panel names
   resolved by name in the project, the plan's raw retention, and when each named
   service last reported, before any scan starts: an `AsyncSession` must not be shared
   between concurrent tasks, so scans get a repository and never the session.
2. **Stamp "last opened"**, at most hourly and never in a support view (FR37).
3. **Commit.** A batch may take many seconds, and must not pin a Postgres connection
   while it waits on ClickHouse.
4. **Compile.** Each stored panel parses alone (an unknown one reads `unknown_kind`),
   what Postgres or the install already decides needs no scan (`needs_service`, a check
   `not_in_project` or `ambiguous`, `window_too_wide`, `paused_by_operator`), equal
   scans merge, and keys past the scan cap read `over_limit` with no scan.
5. **Scan in layout order**, 4 at once, each through the shared reads (kept, joined or
   led), under one deadline counted from the request's arrival (D25). An abandoned
   request starts nothing more.
6. **Assemble** one state per panel. A failure never takes the page (FR14).

**A draft** (`POST /preview`, T3.1) takes the same path from step 4, and the same
charge: it is validated as a save is (FR7), names no saved row, and stamps nothing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.dashboardpanels.caps import ScanCaps
from nexovue_api.dashboardpanels.scans import (
    RAW_READERS,
    READERS,
    SINGLE_SERIES_READERS,
    CheckLine,
    NodeLine,
    NodeLines,
    Ranked,
    ScanContext,
    ServiceTrends,
    StatementLine,
    Trend,
)
from nexovue_api.dashboardpanels.schemas import (
    AmbiguousPanel,
    EmptyPanel,
    FailedPanel,
    NeedsServicePanel,
    NodeName,
    NotInProjectPanel,
    NumberReading,
    OkPanel,
    OverLimitPanel,
    PanelReadResponse,
    PanelResult,
    PausedByOperatorPanel,
    RefusedPanel,
    Series,
    TimedOutPanel,
    UnknownKindPanel,
    WindowTooWidePanel,
)
from nexovue_api.dashboardpanels.shared_reads import (
    SHARED_READ_STATEMENT_SECONDS,
    Answer,
    Charge,
    FailedRead,
    Outcome,
    ReadKey,
    RefusedRead,
    Served,
    SharedReads,
    TimedOutRead,
)
from nexovue_api.dashboards import queries as dashboard_queries
from nexovue_api.dashboards import service as dashboards
from nexovue_api.dashboards.compile import (
    CHECK_TREND,
    ERROR_GROUPS,
    CompiledPanel,
    ScanKey,
    compile_panel,
    resolve,
    resolve_check,
)
from nexovue_api.dashboards.definition import (
    CheckTrendPanel,
    Definition,
    EndpointTrendPanel,
    KnownPanel,
    NodeTrendPanel,
    ProjectNumberPanel,
    ProjectTrendPanel,
    ServiceNumberPanel,
    ServiceTrendPanel,
    StatementTrendPanel,
    StoredDefinition,
    TopNodesTrendPanel,
    TopServicesTrendPanel,
    UnknownPanel,
    parse_stored,
)
from nexovue_api.dashboards.limits import CODE_LIMITS, DashboardLimits, compile_within
from nexovue_api.dashboards.ranges import RangeId, window_for
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Project
from nexovue_api.deploys import service as deploys
from nexovue_api.ratelimit.policy import PANEL_READ_DEADLINE_SECONDS
from nexovue_api.releases import service as releases
from nexovue_api.services.schemas import OverviewPoint, TrendPoint
from nexovue_api.services.service import CoveredService, CoveredTotals

logger = get_logger(__name__)

# What each signal draws, by the reading's own name, as Overview draws them.
SIGNAL_SERIES: Final[dict[str, tuple[str, ...]]] = {
    "rate": ("requests_per_minute",),
    "errors": ("error_rate",),
    "latency": ("p50_ms", "p95_ms", "p99_ms"),
}
# A top-N chart draws one line a service, so one reading a signal: latency is the p95,
# the reading the "slowest" ranking orders by (`services/queries.SLOWEST_FIRST`).
TOP_SERVICES_SERIES: Final[dict[str, str]] = {
    "rate": "requests_per_minute",
    "errors": "error_rate",
    "latency": "p95_ms",
}
# A check chart's lines, as the synthetics screen draws a check (`synthetics/schemas.py`).
UPTIME_PERCENT: Final = "uptime_percent"
MEAN_DURATION_MS: Final = "mean_duration_ms"
PERCENT: Final = 100.0
# A statement chart's line: the mean duration per bucket, as the database screen's.
STATEMENT_MEAN_MS: Final = "mean_ms"
# A compared number draws its arrow only when its data covers the previous window from
# no later than 5% into it (*guess*; design, *A compared number*).
COMPARE_MIN_COVERAGE: Final = 0.95

Disconnected = Callable[[], Awaitable[bool]]
StoredPanel = KnownPanel | UnknownPanel
Window = tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class PanelReads:
    """The process's caps, kept answers and the install's limits, built once with the app."""

    caps: ScanCaps
    shared: SharedReads
    limits: DashboardLimits = CODE_LIMITS
    # A hosted install knows each organization's plan, whose raw retention bounds a raw
    # panel's window (FR17). A self-hosted one has no plan.
    plan_limits_windows: bool = False

    @classmethod
    def for_install(
        cls,
        *,
        shared_by_organizations: bool,
        limits: DashboardLimits | None = None,
        hosted: bool = False,
    ) -> PanelReads:
        caps = ScanCaps(shared_by_organizations=shared_by_organizations)
        return cls(
            caps=caps,
            shared=SharedReads(caps=caps),
            limits=limits or CODE_LIMITS,
            plan_limits_windows=hosted,
        )


@dataclass(frozen=True, slots=True)
class BatchRequest:
    dashboard_id: uuid.UUID
    project_id: uuid.UUID
    range_id: RangeId | None
    from_time: datetime | None
    to_time: datetime | None
    service: str | None
    panel_ids: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class DraftRequest:
    """A draft's read: what a saved read names, with the definition in place of a row."""

    project_id: uuid.UUID
    range_id: RangeId | None
    from_time: datetime | None
    to_time: datetime | None
    service: str | None
    panel_ids: frozenset[str] | None
    definition: Definition


@dataclass(frozen=True, slots=True)
class _Scanning:
    """What the scans need, once Postgres has been read and let go."""

    identity: Identity
    project: Project
    repository: TenantScopedRepository
    reads: PanelReads
    charge: Charge
    window: Window
    deadline: float
    disconnected: Disconnected
    # A live range's newest bucket is still filling at `now`; a fixed window's is not.
    live: bool
    now: datetime
    context: ScanContext


def _deadline(reads: PanelReads, arrived_at: float | None) -> float:
    """Counted from the request's arrival, so every earlier wait shortens the scans (D25)."""
    return (arrived_at if arrived_at is not None else reads.shared.now()) + (
        PANEL_READ_DEADLINE_SECONDS
    )


async def read_panels(
    db: AsyncSession,
    *,
    identity: Identity,
    repository: TenantScopedRepository,
    reads: PanelReads,
    charge: Charge,
    asked: BatchRequest,
    arrived_at: float | None,
    disconnected: Disconnected,
    correlation_id: str | None,
    now: datetime,
) -> PanelReadResponse:
    started = time.monotonic()
    deadline = _deadline(reads, arrived_at)

    # 1. Postgres, all of it, before any scan.
    dashboard = await dashboards.readable_dashboard(
        db, org_id=identity.org_id, dashboard_id=asked.dashboard_id
    )
    project = await dashboards.readable_project(
        db, org_id=identity.org_id, project_id=asked.project_id
    )
    window = window_for(asked.range_id, asked.from_time, asked.to_time, now=now)
    plan, last_seen, context = await _plan_with_pre_reads(
        db,
        identity=identity,
        project=project,
        reads=reads,
        stored=parse_stored(dashboard.definition),
        service=asked.service,
        panel_ids=asked.panel_ids,
        window=window,
        now=now,
    )
    # 2 and 3: stamp, then let go of the connection before ClickHouse is asked anything.
    dashboards.stamp_opened(dashboard, identity=identity, now=now)
    await db.commit()

    panels, outcomes = await _scan_and_assemble(
        _Scanning(
            identity=identity,
            project=project,
            repository=repository,
            reads=reads,
            charge=charge,
            window=window,
            deadline=deadline,
            disconnected=disconnected,
            live=asked.range_id is not None,
            now=now,
            context=context,
        ),
        plan,
        last_seen,
    )
    _log(dashboard.id, plan, outcomes, panels, started, correlation_id)
    return PanelReadResponse(
        correlation_id=correlation_id, window_start=window[0], window_end=window[1], panels=panels
    )


async def preview_panels(
    db: AsyncSession,
    *,
    identity: Identity,
    repository: TenantScopedRepository,
    reads: PanelReads,
    charge: Charge,
    asked: DraftRequest,
    arrived_at: float | None,
    disconnected: Disconnected,
    correlation_id: str | None,
    now: datetime,
) -> PanelReadResponse:
    """A draft's panels, read and charged as a saved dashboard's are (FR10, AC135).

    Refused as a save would refuse it, before anything is read (FR7). It reads no saved
    row, so it moves no "last opened".
    """
    started = time.monotonic()
    deadline = _deadline(reads, arrived_at)
    compile_within(asked.definition, reads.limits)

    project = await dashboards.readable_project(
        db, org_id=identity.org_id, project_id=asked.project_id
    )
    window = window_for(asked.range_id, asked.from_time, asked.to_time, now=now)
    # Read back as a stored definition is, so a draft and its saved form cannot differ.
    plan, last_seen, context = await _plan_with_pre_reads(
        db,
        identity=identity,
        project=project,
        reads=reads,
        stored=parse_stored(asked.definition.model_dump(mode="json")),
        service=asked.service,
        panel_ids=asked.panel_ids,
        window=window,
        now=now,
    )
    await db.commit()

    panels, outcomes = await _scan_and_assemble(
        _Scanning(
            identity=identity,
            project=project,
            repository=repository,
            reads=reads,
            charge=charge,
            window=window,
            deadline=deadline,
            disconnected=disconnected,
            live=asked.range_id is not None,
            now=now,
            context=context,
        ),
        plan,
        last_seen,
    )
    _log(None, plan, outcomes, panels, started, correlation_id)
    return PanelReadResponse(
        correlation_id=correlation_id, window_start=window[0], window_end=window[1], panels=panels
    )


# ------------------------------------------------------------------ the plan


@dataclass(frozen=True, slots=True)
class _Entry:
    """One panel to answer: its keys once resolved, or the state decided with no scan."""

    panel: StoredPanel
    compiled: CompiledPanel
    scan: ScanKey | None = None
    compare: ScanKey | None = None
    decided: PanelResult | None = None

    @property
    def keys(self) -> tuple[ScanKey, ...]:
        return tuple(key for key in (self.scan, self.compare) if key is not None)


@dataclass(frozen=True, slots=True)
class _Plan:
    entries: list[_Entry]
    # Distinct keys within the cap, in layout order, and those past it.
    scans: tuple[ScanKey, ...]
    over_limit: frozenset[ScanKey]
    window: Window


@dataclass(frozen=True, slots=True)
class _PreRead:
    """What Postgres decided before any scan."""

    picked: str | None
    # Each check name a panel names, and the ids of the project's checks by that name.
    checks: dict[str, list[str]]
    # The plan's raw retention in days, where a plan bounds a raw panel's window.
    raw_retention_days: int | None


async def _plan_with_pre_reads(
    db: AsyncSession,
    *,
    identity: Identity,
    project: Project,
    reads: PanelReads,
    stored: StoredDefinition,
    service: str | None,
    panel_ids: frozenset[str] | None,
    window: Window,
    now: datetime,
) -> tuple[_Plan, dict[str, datetime], ScanContext]:
    """Every Postgres read the batch needs, in sequence, and the plan they decide."""
    chosen = [panel for panel in stored.panels if panel_ids is None or panel.id in panel_ids]
    names = sorted({panel.settings.check for panel in chosen if isinstance(panel, CheckTrendPanel)})
    checks = await dashboard_queries.checks_named(
        db, org_id=identity.org_id, project_id=project.id, names=names
    )
    raw = any(key.reader in RAW_READERS for panel in chosen for key in compile_panel(panel).keys)
    retention = (
        await dashboards.plan_retention(db, identity=identity, now=now)
        if raw and reads.plan_limits_windows
        else None
    )
    plan = _plan(
        chosen,
        pre_read=_PreRead(
            picked=service if stored.service_picker else None,
            checks=checks,
            raw_retention_days=None if retention is None else retention.raw_days,
        ),
        limits=reads.limits,
        window=window,
    )
    last_seen = await dashboard_queries.last_seen_of(
        db,
        org_id=identity.org_id,
        project_id=project.id,
        services=sorted({key.service for key in plan.scans if key.service is not None}),
    )
    return plan, last_seen, await _scan_context(db, identity, project, plan, window)


async def _scan_context(
    db: AsyncSession, identity: Identity, project: Project, plan: _Plan, window: Window
) -> ScanContext:
    """The deploys and coverage an errors table's rows carry, read as its screen reads them,
    and only when one is on the page."""
    if not any(key.reader == ERROR_GROUPS for key in plan.scans):
        return ScanContext()
    return ScanContext(
        deploys=await deploys.history_for_window(
            db,
            org_id=identity.org_id,
            project_id=project.id,
            from_time=window[0],
            to_time=window[1],
        ),
        coverage=await releases.coverage_book(db, org_id=identity.org_id, project_id=project.id),
    )


def _plan(
    panels: Sequence[StoredPanel], *, pre_read: _PreRead, limits: DashboardLimits, window: Window
) -> _Plan:
    entries = [_entry(panel, pre_read=pre_read, limits=limits, window=window) for panel in panels]
    keys = tuple(dict.fromkeys(key for entry in entries for key in entry.keys))
    return _Plan(
        entries=entries,
        scans=keys[: limits.max_scans_per_read],
        over_limit=frozenset(keys[limits.max_scans_per_read :]),
        window=window,
    )


def _entry(
    panel: StoredPanel, *, pre_read: _PreRead, limits: DashboardLimits, window: Window
) -> _Entry:
    """What a panel needs, or the state Postgres and the install decide without a scan."""
    compiled = compile_panel(panel)
    if isinstance(panel, UnknownPanel):
        return _Entry(panel, compiled, decided=UnknownKindPanel())
    if compiled.scan is None:
        return _Entry(panel, compiled)  # a note or open findings: the console draws it
    if not limits.scans_enabled:
        return _Entry(panel, compiled, decided=PausedByOperatorPanel(kind=panel.kind))
    scan = resolve(compiled.scan, picked=pre_read.picked)
    if scan is None:
        return _Entry(panel, compiled, decided=NeedsServicePanel(kind=panel.kind))
    if scan.reader == CHECK_TREND:
        found = pre_read.checks.get(scan.check or "", [])
        if not found:
            # Identical whether the name is in another project or nowhere (AC22).
            return _Entry(panel, compiled, decided=NotInProjectPanel(kind=panel.kind))
        if len(found) > 1:
            return _Entry(panel, compiled, decided=AmbiguousPanel(kind=panel.kind))
        scan = resolve_check(scan, found[0])
    if (limit := _window_limit_days(scan, pre_read, limits)) is not None and (
        window[1] - window[0] > timedelta(days=limit)
    ):
        return _Entry(
            panel, compiled, decided=WindowTooWidePanel(kind=panel.kind, limit_days=limit)
        )
    compare = resolve(compiled.compare, picked=pre_read.picked)
    return _Entry(panel, compiled, scan=scan, compare=compare)


def _window_limit_days(scan: ScanKey, pre_read: _PreRead, limits: DashboardLimits) -> int | None:
    """A raw reader's widest window: the lesser of its safety limit and the plan's (FR17)."""
    if scan.reader not in RAW_READERS:
        return None
    safety = (
        limits.single_series_raw_window_days
        if scan.reader in SINGLE_SERIES_READERS
        else limits.raw_window_days
    )
    if pre_read.raw_retention_days is None:
        return safety
    return min(safety, pre_read.raw_retention_days)


def _window_of(key: ScanKey, window: Window) -> Window:
    """A key's own window: the read's, or for a compared number the one before it."""
    if not key.previous:
        return window
    start, end = window
    return start - (end - start), start


# ------------------------------------------------------------- scan, assemble


async def _scan_and_assemble(
    scanning: _Scanning, plan: _Plan, last_seen: dict[str, datetime]
) -> tuple[dict[str, PanelResult], dict[ScanKey, Outcome]]:
    """Steps 5 and 6: scan in layout order, then one state per panel."""
    reads, deadline = scanning.reads, scanning.deadline
    permits = asyncio.Semaphore(reads.limits.scans_at_once)

    async def scan(key: ScanKey) -> Outcome:
        window_start, window_end = _window_of(key, scanning.window)
        try:
            await asyncio.wait_for(permits.acquire(), timeout=max(deadline - reads.shared.now(), 0))
        except TimeoutError:
            return TimedOutRead()
        try:
            if await scanning.disconnected():
                # Nobody will read the answer: start nothing more.
                return TimedOutRead()
            reader = READERS[key.reader]

            async def run() -> Answer:
                return await reader(
                    scanning.repository.bounded_by(SHARED_READ_STATEMENT_SECONDS),
                    str(scanning.project.id),
                    key,
                    window_start,
                    window_end,
                    scanning.context,
                )

            return await reads.shared.read(
                ReadKey(
                    org_id=str(scanning.identity.org_id),
                    project_id=str(scanning.project.id),
                    scan=key,
                    window_start=window_start,
                    window_end=window_end,
                ),
                run=run,
                raw=key.reader in RAW_READERS,
                charge=scanning.charge,
                deadline=deadline,
            )
        finally:
            permits.release()

    outcomes = dict(
        zip(plan.scans, await asyncio.gather(*(scan(key) for key in plan.scans)), strict=True)
    )
    panels: dict[str, PanelResult] = {}
    filling_at = scanning.now if scanning.live else None
    for entry in plan.entries:
        result = _result(entry, plan, outcomes, last_seen, filling_at)
        if result is not None:
            panels[entry.panel.id] = result
    return panels, outcomes


def _result(
    entry: _Entry,
    plan: _Plan,
    outcomes: dict[ScanKey, Outcome],
    last_seen: dict[str, datetime],
    filling_at: datetime | None,
) -> PanelResult | None:
    panel = entry.panel
    if entry.decided is not None:
        return entry.decided
    if entry.scan is None:
        return None
    if any(key in plan.over_limit for key in entry.keys):
        return OverLimitPanel(kind=panel.kind)
    served: list[Served] = []
    for key in entry.keys:
        outcome = outcomes[key]
        if isinstance(outcome, RefusedRead):
            return RefusedPanel(kind=panel.kind, retry_after_seconds=outcome.retry_after_seconds)
        if isinstance(outcome, TimedOutRead):
            return TimedOutPanel(kind=panel.kind)
        if isinstance(outcome, FailedRead):
            return FailedPanel(kind=panel.kind)
        served.append(outcome)
    drawing = _Drawing(
        panel=panel,
        key=entry.scan,
        served=served[0],
        compared=served[1] if len(served) > 1 else None,
        last_seen=last_seen,
        filling_at=filling_at,
        previous_window=_window_of(entry.compare, plan.window) if entry.compare else None,
    )
    try:
        return drawing.result()
    except TypeError as error:
        # The panel boundary: an answer this panel cannot draw fails it alone (FR14).
        logger.warning(
            "a dashboard panel could not be drawn", kind=panel.kind, error=type(error).__name__
        )
        return FailedPanel(kind=panel.kind)


@dataclass(frozen=True, slots=True)
class _Drawing:
    """One panel's answer turned into its state: a chart, a number or a table."""

    panel: StoredPanel
    key: ScanKey
    served: Served
    compared: Served | None
    last_seen: dict[str, datetime]
    filling_at: datetime | None
    previous_window: Window | None

    @property
    def read_at(self) -> datetime:
        return self.served.answer.read_at

    def result(self) -> PanelResult:
        value = self.served.answer.value
        if isinstance(value, CoveredTotals | CoveredService):
            return self._number(value)
        if isinstance(value, Ranked):
            return self._table(value)
        return self._chart(value)

    def _nothing(self) -> PanelResult:
        """Read, and nothing in the window. A service's panel tells a service this project
        has no record of from a quiet one, by the usage meter's record (FR16)."""
        if self.key.service is None:
            return EmptyPanel(kind=self.panel.kind, read_at=self.read_at)
        seen = self.last_seen.get(self.key.service)
        if seen is None:
            # No record here, identical whether it reports elsewhere or nowhere (FR16).
            return NotInProjectPanel(kind=self.panel.kind)
        return EmptyPanel(kind=self.panel.kind, read_at=self.read_at, last_seen_at=seen)

    def _chart(self, value: object) -> PanelResult:
        lines, bucket_seconds = _lines(self.panel, value)
        buckets = sorted({at for line in lines for at, _ in line.points})
        if not buckets:
            return self._nothing()
        return OkPanel(
            kind=self.panel.kind,
            read_at=self.read_at,
            bucket_seconds=bucket_seconds,
            data_starts_at=buckets[0],
            data_ends_at=buckets[-1],
            incomplete_from=_filling(buckets[-1], bucket_seconds, self.filling_at),
            series=lines,
        )

    def _number(self, value: CoveredTotals | CoveredService) -> PanelResult:
        if not isinstance(self.panel, ProjectNumberPanel | ServiceNumberPanel):
            raise TypeError(f"a {self.panel.kind} panel cannot draw a number")
        reading = _reading_of(value)
        if reading is None:
            return self._nothing()
        measure = self.panel.settings.measure
        number = NumberReading(measure=measure, value=float(getattr(reading, measure)))
        if self.compared is not None and self.previous_window is not None:
            number = _compared(number, self.compared.answer.value, self.previous_window)
        return OkPanel(
            kind=self.panel.kind,
            read_at=self.read_at,
            bucket_seconds=0,
            data_starts_at=_utc(value.first_minute),
            data_ends_at=None,
            number=number,
        )

    def _table(self, value: Ranked) -> PanelResult:
        if not value.rows:
            return self._nothing()
        # Each table shows its own first rows of the ranking; web vitals are all five.
        shown: int | None = getattr(getattr(self.panel, "settings", None), "rows", None)
        return OkPanel(
            kind=self.panel.kind,
            read_at=self.read_at,
            bucket_seconds=0,
            data_starts_at=None,
            data_ends_at=None,
            rows=list(value.rows[:shown]),
        )


def _reading_of(value: CoveredTotals | CoveredService) -> object | None:
    """The row a number reads, or None when nothing reported in the window: a project
    whose totals count no service answers zeros, which must read `empty`, never 0."""
    if isinstance(value, CoveredTotals):
        return value.totals if value.totals.service_count > 0 else None
    return value.service


def _compared(number: NumberReading, previous: object, window: Window) -> NumberReading:
    """The previous window's value, only where its data covers that window (AC73)."""
    if not isinstance(previous, CoveredTotals | CoveredService):
        raise TypeError(f"a compared number cannot read {type(previous).__name__}")
    reading = _reading_of(previous)
    first = _utc(previous.first_minute)
    if reading is None or first is None:
        return number
    start, end = window
    coverage = (end - max(first, start)) / (end - start)
    comparable = coverage >= COMPARE_MIN_COVERAGE
    return number.model_copy(
        update={
            "previous": float(getattr(reading, number.measure)) if comparable else None,
            "previous_coverage": round(coverage, 4),
        }
    )


def _utc(moment: datetime | None) -> datetime | None:
    """The store's times come back without a zone, and are UTC (`wire.to_iso_millis`)."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _filling(newest: datetime, bucket_seconds: int, filling_at: datetime | None) -> datetime | None:
    """The newest bucket, if it has not ended by now on a live window (FR66).

    A live window's end is rounded up to the next minute, so its last bucket holds only
    part of its time, while the rate divides by the whole bucket.
    """
    if filling_at is None:
        return None
    start = _utc(newest) or newest
    return start if start + timedelta(seconds=bucket_seconds) > filling_at else None


def _lines(panel: StoredPanel, value: object) -> tuple[list[Series], int]:
    """A chart's lines and bucket width from its reader's answer, by kind."""
    if isinstance(panel, TopServicesTrendPanel) and isinstance(value, ServiceTrends):
        reading = TOP_SERVICES_SERIES[panel.settings.signal]
        return [
            Series(
                label=reading,
                service=line.service_name,
                points=[(point.bucket, getattr(point, reading)) for point in line.points],
            )
            for line in value.services
        ], value.bucket_seconds
    if isinstance(panel, NodeTrendPanel) and isinstance(value, NodeLine):
        line = Series(
            label=panel.settings.metric,
            points=[(point.bucket, point.value) for point in value.points],
        )
        return [line], value.bucket_seconds
    if isinstance(panel, TopNodesTrendPanel) and isinstance(value, NodeLines):
        return [
            Series(
                label=panel.settings.metric,
                node=NodeName(
                    host=node.host,
                    container=node.container,
                    reporter=node.reporter,
                    reporter_instance=node.reporter_instance,
                ),
                points=[(point.bucket, point.value) for point in points],
            )
            for node, points in value.lines
        ], value.bucket_seconds
    if isinstance(panel, CheckTrendPanel) and isinstance(value, CheckLine):
        return [_check_series(value, panel.settings.signal)], value.bucket_seconds
    if isinstance(panel, StatementTrendPanel) and isinstance(value, StatementLine):
        line = Series(
            label=STATEMENT_MEAN_MS,
            points=[(point.bucket, point.mean_ms) for point in value.points],
        )
        return [line], value.bucket_seconds
    if isinstance(panel, ProjectTrendPanel | ServiceTrendPanel | EndpointTrendPanel) and isinstance(
        value, Trend
    ):
        return _series(value.points, panel.settings.signal), value.bucket_seconds
    raise TypeError(f"a {panel.kind} panel cannot draw {type(value).__name__}")


def _check_series(value: CheckLine, signal: str) -> Series:
    """A check's uptime as the share of its counted runs that succeeded, or the mean
    duration of its successful timed runs. A bucket with no run, or no duration, is a
    gap, never a zero: a check drawn at zero reads as the site getting instantly fast."""
    if signal == "uptime":
        return Series(
            label=UPTIME_PERCENT,
            points=[
                (point.bucket, PERCENT * point.successful_runs / point.runs)
                for point in value.points
                if point.runs > 0
            ],
        )
    return Series(
        label=MEAN_DURATION_MS,
        points=[
            (point.bucket, point.mean_duration_ms)
            for point in value.points
            if point.mean_duration_ms is not None
        ],
    )


def _series(points: Sequence[OverviewPoint | TrendPoint], signal: str) -> list[Series]:
    return [
        Series(label=reading, points=[(point.bucket, getattr(point, reading)) for point in points])
        for reading in SIGNAL_SERIES[signal]
    ]


def _log(
    dashboard_id: uuid.UUID | None,
    plan: _Plan,
    outcomes: dict[ScanKey, Outcome],
    panels: dict[str, PanelResult],
    started: float,
    correlation_id: str | None,
) -> None:
    """One line per batch, naming no title and no service (design, *Observability*). A
    draft has no dashboard id."""
    sources = Counter(
        outcome.source for outcome in outcomes.values() if isinstance(outcome, Served)
    )
    states = Counter(result.state for result in panels.values())
    wanted = sum(len(entry.keys) for entry in plan.entries)
    logger.info(
        "dashboard batch read",
        dashboard_id=None if dashboard_id is None else str(dashboard_id),
        draft=dashboard_id is None,
        panels=len(plan.entries),
        scans_run=sources["read"],
        merged=wanted - len(plan.scans) - len(plan.over_limit),
        kept_answers_used=sources["kept"],
        joined=sources["joined"],
        refused=states["refused"],
        timed_out=states["timed_out"],
        failed=states["failed"],
        over_limit=states["over_limit"],
        paused=states["paused_by_operator"],
        total_ms=round((time.monotonic() - started) * 1000),
        correlation_id=correlation_id,
    )
