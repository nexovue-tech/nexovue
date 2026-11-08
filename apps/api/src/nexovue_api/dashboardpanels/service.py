"""Every panel of a saved dashboard in one request (F20, design section 5).

The sequence, and why each step is where it is:

1. **Postgres first, all of it.** The dashboard, the project, and when each named
   service last reported, before any scan starts: an `AsyncSession` must not be shared
   between concurrent tasks, so scans get a repository and never the session.
2. **Stamp "last opened"**, at most hourly and never in a support view (FR37).
3. **Commit.** A batch may take many seconds, and must not pin a Postgres connection
   while it waits on ClickHouse.
4. **Compile.** Each stored panel parses alone (an unknown one reads `unknown_kind`),
   equal scans merge, and keys past the scan cap read `over_limit` with no scan.
5. **Scan in layout order**, 4 at once, each through the shared reads (kept, joined or
   led), under one deadline counted from the request's arrival (D25). An abandoned
   request starts nothing more.
6. **Assemble** one state per panel. A failure never takes the page (FR14).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.dashboardpanels.caps import ScanCaps
from nexovue_api.dashboardpanels.scans import RAW_READERS, READERS, Trend
from nexovue_api.dashboardpanels.schemas import (
    EmptyPanel,
    FailedPanel,
    NeedsServicePanel,
    NotInProjectPanel,
    OkPanel,
    OverLimitPanel,
    PanelReadResponse,
    PanelResult,
    RefusedPanel,
    Series,
    TimedOutPanel,
    UnknownKindPanel,
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
    SERVICE_TREND,
    CompiledPanel,
    ScanKey,
    compile_definition,
    resolve,
)
from nexovue_api.dashboards.definition import (
    NotePanel,
    ProjectTrendPanel,
    ServiceTrendPanel,
    StoredDefinition,
    UnknownPanel,
    parse_stored,
)
from nexovue_api.dashboards.ranges import RangeId, window_for
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.ratelimit.policy import MAX_SCANS_PER_READ, PANEL_READ_DEADLINE_SECONDS
from nexovue_api.services.schemas import OverviewPoint, TrendPoint

logger = get_logger(__name__)

# One batch's scans at once, below the process's 8 ClickHouse connections (a *guess*
# until plan T5.7). The process cap holds every batch together.
PANEL_SCANS_AT_ONCE: Final = 4

# What each signal draws, by the reading's own name, as Overview draws them.
SIGNAL_SERIES: Final[dict[str, tuple[str, ...]]] = {
    "rate": ("requests_per_minute",),
    "errors": ("error_rate",),
    "latency": ("p50_ms", "p95_ms", "p99_ms"),
}

Disconnected = Callable[[], Awaitable[bool]]
StoredPanel = ProjectTrendPanel | ServiceTrendPanel | NotePanel | UnknownPanel


@dataclass(frozen=True, slots=True)
class PanelReads:
    """The process's caps and kept answers, built once with the app."""

    caps: ScanCaps
    shared: SharedReads

    @classmethod
    def for_install(cls, *, shared_by_organizations: bool) -> PanelReads:
        caps = ScanCaps(shared_by_organizations=shared_by_organizations)
        return cls(caps=caps, shared=SharedReads(caps=caps))


@dataclass(frozen=True, slots=True)
class BatchRequest:
    dashboard_id: uuid.UUID
    project_id: uuid.UUID
    range_id: RangeId | None
    from_time: datetime | None
    to_time: datetime | None
    service: str | None
    panel_ids: frozenset[str] | None


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
    deadline = (arrived_at if arrived_at is not None else reads.shared.now()) + (
        PANEL_READ_DEADLINE_SECONDS
    )

    # 1. Postgres, all of it, before any scan.
    dashboard = await dashboards.readable_dashboard(
        db, org_id=identity.org_id, dashboard_id=asked.dashboard_id
    )
    project = await dashboards.readable_project(
        db, org_id=identity.org_id, project_id=asked.project_id
    )
    window_start, window_end = window_for(asked.range_id, asked.from_time, asked.to_time, now=now)
    stored = parse_stored(dashboard.definition)
    plan = _plan(stored, asked)
    last_seen = await dashboard_queries.last_seen_of(
        db,
        org_id=identity.org_id,
        project_id=project.id,
        services=sorted({key.service for key in plan.scans if key.service is not None}),
    )
    # 2 and 3: stamp, then let go of the connection before ClickHouse is asked anything.
    dashboards.stamp_opened(dashboard, identity=identity, now=now)
    await db.commit()

    # 5. Scan, in layout order.
    def key_of(scan: ScanKey) -> ReadKey:
        return ReadKey(
            org_id=str(identity.org_id),
            project_id=str(project.id),
            scan=scan,
            window_start=window_start,
            window_end=window_end,
        )

    permits = asyncio.Semaphore(PANEL_SCANS_AT_ONCE)

    async def scan(key: ScanKey) -> Outcome:
        try:
            await asyncio.wait_for(permits.acquire(), timeout=max(deadline - reads.shared.now(), 0))
        except TimeoutError:
            return TimedOutRead()
        try:
            if await disconnected():
                # Nobody will read the answer: start nothing more.
                return TimedOutRead()
            reader = READERS[key.reader]

            async def run() -> Answer:
                return await reader(
                    repository.bounded_by(SHARED_READ_STATEMENT_SECONDS),
                    str(project.id),
                    key,
                    window_start,
                    window_end,
                )

            return await reads.shared.read(
                key_of(key),
                run=run,
                raw=key.reader in RAW_READERS,
                charge=charge,
                deadline=deadline,
            )
        finally:
            permits.release()

    outcomes = dict(
        zip(plan.scans, await asyncio.gather(*(scan(key) for key in plan.scans)), strict=True)
    )

    # 6. Assemble.
    panels: dict[str, PanelResult] = {}
    for panel, compiled, resolved in plan.panels:
        result = _result(panel, compiled, resolved, plan, outcomes, last_seen)
        if result is not None:
            panels[panel.id] = result
    _log(dashboard.id, plan, outcomes, panels, started, correlation_id)
    return PanelReadResponse(
        correlation_id=correlation_id,
        window_start=window_start,
        window_end=window_end,
        panels=panels,
    )


@dataclass(frozen=True, slots=True)
class _Plan:
    panels: list[tuple[StoredPanel, CompiledPanel, ScanKey | None]]
    # Distinct keys within the cap, in layout order, and those past it.
    scans: tuple[ScanKey, ...]
    over_limit: frozenset[ScanKey]


def _plan(stored: StoredDefinition, asked: BatchRequest) -> _Plan:
    compiled = compile_definition(stored)
    picked = asked.service if stored.service_picker else None
    chosen = [
        (panel, compiled_panel, resolve(compiled_panel.scan, picked=picked))
        for panel, compiled_panel in zip(stored.panels, compiled.panels, strict=True)
        if asked.panel_ids is None or panel.id in asked.panel_ids
    ]
    keys = tuple(dict.fromkeys(key for _, _, key in chosen if key is not None))
    return _Plan(
        panels=chosen,
        scans=keys[:MAX_SCANS_PER_READ],
        over_limit=frozenset(keys[MAX_SCANS_PER_READ:]),
    )


def _result(
    panel: StoredPanel,
    compiled: CompiledPanel,
    resolved: ScanKey | None,
    plan: _Plan,
    outcomes: dict[ScanKey, Outcome],
    last_seen: dict[str, datetime],
) -> PanelResult | None:
    if isinstance(panel, UnknownPanel):
        return UnknownKindPanel()
    if compiled.scan is None:
        return None  # a note: the console draws it from the definition
    if resolved is None:
        return NeedsServicePanel(kind=panel.kind)
    if resolved in plan.over_limit:
        return OverLimitPanel(kind=panel.kind)
    outcome = outcomes[resolved]
    if isinstance(outcome, RefusedRead):
        return RefusedPanel(kind=panel.kind, retry_after_seconds=outcome.retry_after_seconds)
    if isinstance(outcome, TimedOutRead):
        return TimedOutPanel(kind=panel.kind)
    if isinstance(outcome, FailedRead):
        return FailedPanel(kind=panel.kind)
    return _drawn(panel, resolved, outcome, last_seen)


def _drawn(
    panel: StoredPanel, key: ScanKey, served: Served, last_seen: dict[str, datetime]
) -> PanelResult:
    trend = served.answer.value
    assert isinstance(trend, Trend) and isinstance(panel, ProjectTrendPanel | ServiceTrendPanel)
    if not trend.points:
        if key.reader != SERVICE_TREND:
            return EmptyPanel(kind=panel.kind, read_at=served.answer.read_at)
        seen = last_seen.get(key.service or "")
        if seen is None:
            # No record here, identical whether it reports elsewhere or nowhere (FR16).
            return NotInProjectPanel(kind=panel.kind)
        return EmptyPanel(kind=panel.kind, read_at=served.answer.read_at, last_seen_at=seen)
    return OkPanel(
        kind=panel.kind,
        read_at=served.answer.read_at,
        bucket_seconds=trend.bucket_seconds,
        data_starts_at=trend.points[0].bucket,
        data_ends_at=trend.points[-1].bucket,
        series=_series(trend.points, panel.settings.signal),
    )


def _series(points: Sequence[OverviewPoint | TrendPoint], signal: str) -> list[Series]:
    return [
        Series(label=reading, points=[(point.bucket, getattr(point, reading)) for point in points])
        for reading in SIGNAL_SERIES[signal]
    ]


def _log(
    dashboard_id: uuid.UUID,
    plan: _Plan,
    outcomes: dict[ScanKey, Outcome],
    panels: dict[str, PanelResult],
    started: float,
    correlation_id: str | None,
) -> None:
    """One line per batch, naming no title and no service (design, *Observability*)."""
    sources = Counter(
        outcome.source for outcome in outcomes.values() if isinstance(outcome, Served)
    )
    states = Counter(result.state for result in panels.values())
    scanned = sum(1 for _, _, key in plan.panels if key is not None)
    logger.info(
        "dashboard batch read",
        dashboard_id=str(dashboard_id),
        panels=len(plan.panels),
        scans_run=sources["read"],
        merged=scanned - len(plan.scans) - len(plan.over_limit),
        kept_answers_used=sources["kept"],
        joined=sources["joined"],
        refused=states["refused"],
        timed_out=states["timed_out"],
        failed=states["failed"],
        over_limit=states["over_limit"],
        total_ms=round((time.monotonic() - started) * 1000),
        correlation_id=correlation_id,
    )
