"""Detection and its alerts never move for F3's sake (plan T1.13; spec BR1, AC48's Step 1
arms; design, *Failure handling*).

With fixed readings and a fixed clock, one outage is judged in a fresh tenant per arm, and
a named projection of `findings` and `notifications`, ids and times normalized, is
compared across arms: F3's evidence job never run (as if unregistered, AI off), run
normally, broken (a gather that always raises), with the evidence store's reads failing
while detection's fixed readings are still served ("ClickHouse down", as AC48 defines it),
and with the evidence queue backed up (its jobs left waiting). The opened and resolved
rows must be the same in all of them.

The one way F3 can stop an opening is the `jobs` insert, which shares the opening's
transaction (A1). That must fail loudly, never quietly: the run raises, and the opening
is not half-written.

Must stand if the feature stops at Step 2. This suite fails rather than skips when the
stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, add_rule, anomaly_rule, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from detection_history import (
    CLICKHOUSE_DSN,
    MINUTE,
    ONSET,
    PAST_THE_MINIMUM,
    POSTGRES_DSN,
    READER_DSN,
    ROLLUP,
    SUSTAIN,
)
from detection_history import baselines_for as _baselines
from detection_history import judge_through as _judge_through
from detection_history import seed as _seed
from detection_history import seed_four_weeks as _seed_four_weeks
from detection_history import span as _span
from live import require_stack
from nexovue_platform.evidence import GATHER_EVIDENCE_TASK
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import project_job
from nexovue_scheduler.detection.store import DetectionStore
from nexovue_scheduler.evidence.gather import gather
from nexovue_scheduler.evidence.store import EvidenceStore, connect_store
from nexovue_scheduler.jobs import queue


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="apps/scheduler/tests/test_detection_unmoved_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)

# The projection compared (AC48): what a person and a channel see of a finding.
_FINDINGS: Final = text(
    """
    SELECT scope, service_name, severity, state, closed_reason, muted, flapping, title,
           signals, services, started_at, closed_at
    FROM findings WHERE org_id = :org_id ORDER BY started_at, service_name
    """
)
_NOTIFICATIONS: Final = text(
    """
    SELECT notifications.kind, notifications.severity, notifications.sequence,
           channels.kind AS channel_kind
    FROM notifications
    JOIN notification_channels AS channels ON channels.id = notifications.channel_id
    WHERE notifications.org_id = :org_id AND notifications.finding_id IS NOT NULL
    ORDER BY notifications.kind, notifications.sequence, channels.kind
    """
)


class _ReadsFail:
    """The evidence store with every read failing, as a down ClickHouse would."""

    def for_project(self, org_id: object, project_id: object) -> Any:
        return self

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def failing(*_: object, **__: object) -> Any:
            raise ConnectionError("ClickHouse is down for the evidence job")

        return failing


async def _projection(engine: AsyncEngine, tenant: AlertTenant) -> tuple[list[Any], list[Any]]:
    async with engine.connect() as connection:
        findings = [
            tuple(row) for row in await connection.execute(_FINDINGS, {"org_id": tenant.org_id})
        ]
        notifications = [
            tuple(row)
            for row in await connection.execute(_NOTIFICATIONS, {"org_id": tenant.org_id})
        ]
    return findings, notifications


async def _queued_gathers(engine: AsyncEngine, tenant: AlertTenant) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT kwargs FROM jobs WHERE org_id = :org_id AND task = :task ORDER BY run_after"
            ),
            {"org_id": tenant.org_id, "task": GATHER_EVIDENCE_TASK},
        )
        return [row.kwargs for row in rows]


async def _outage(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, tenant: AlertTenant
) -> None:
    """The same outage, on the same fixed clock, in every arm: open and recover, told to
    the owners through an anomaly rule."""
    async with engine.begin() as connection:
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[tenant.owners_channel_id],
        )
    _seed_four_weeks(administrator, tenant)
    await _baselines(engine, store, tenant)
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    slow = {ONSET + step * MINUTE: PAST_THE_MINIMUM for step in range(SUSTAIN)}
    _seed(administrator, tenant, span, slower_by=slow)
    await _judge_through(
        engine, store, tenant, _span(ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30))
    )


async def _run_gathers(
    engine: AsyncEngine, tenant: AlertTenant, evidence: EvidenceStore | Any
) -> None:
    for job in await _queued_gathers(engine, tenant):
        await gather(
            engine,
            evidence,
            finding_id=uuid.UUID(job["finding_id"]),
            org_id=tenant.org_id,
            reason=job["reason"],
            deployment="self-hosted",
            now=ONSET + timedelta(minutes=31),
        )


async def _never(engine: AsyncEngine, tenant: AlertTenant) -> None:
    """As if F3's task were unregistered, or the evidence queue backed up: nothing runs."""


async def _normally(engine: AsyncEngine, tenant: AlertTenant) -> None:
    await _run_gathers(engine, tenant, connect_store(READER_DSN))


class _GatherRaises:
    """A store whose every use raises outside the reads' own guard: the gather itself
    fails, as a broken task would."""

    def for_project(self, org_id: object, project_id: object) -> Any:
        raise RuntimeError("a gather that always raises")


async def _broken(engine: AsyncEngine, tenant: AlertTenant) -> None:
    with pytest.raises(RuntimeError, match="always raises"):
        await _run_gathers(engine, tenant, _GatherRaises())


async def _clickhouse_down(engine: AsyncEngine, tenant: AlertTenant) -> None:
    await _run_gathers(engine, tenant, _ReadsFail())


ARMS: Final[dict[str, Callable[[AsyncEngine, AlertTenant], Awaitable[None]]]] = {
    "unregistered": _never,
    "ai_off": _normally,
    "broken": _broken,
    "clickhouse_down": _clickhouse_down,
    "backed_up": _never,
}


async def test_detection_and_its_alerts_are_the_same_in_every_step_1_arm(
    engine: AsyncEngine, store: DetectionStore, administrator: Client
) -> None:
    """AC48, Step 1: the opened and resolved rows match whatever the evidence job does."""
    projections: dict[str, tuple[list[Any], list[Any]]] = {}
    for arm, evidence in ARMS.items():
        tenant = await make_tenant(engine, with_check=False)
        for table in (ROLLUP, "endpoint_stats_1m", "detection_readings"):
            delete_after_run(table, str(tenant.org_id))
        try:
            await _outage(engine, store, administrator, tenant)
            await evidence(engine, tenant)
            projections[arm] = await _projection(engine, tenant)
        finally:
            await drop_tenant(engine, tenant)

    reference = projections["unregistered"]
    findings, notifications = reference
    assert [row[3] for row in findings] == ["closed"], (
        "the outage must open and resolve one finding"
    )
    assert {row[0] for row in notifications} >= {"opened", "resolved"}
    for arm, projection in projections.items():
        assert projection == reference, f"the {arm} arm moved detection or its alerts"


async def test_a_failed_jobs_insert_aborts_the_opening_loudly(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC48: the one way F3 touches an opening is its job, in the opening's transaction.
    A failed insert raises out of the run, and leaves no finding behind."""
    tenant = await make_tenant(engine, with_check=False)
    for table in (ROLLUP, "endpoint_stats_1m", "detection_readings"):
        delete_after_run(table, str(tenant.org_id))
    try:
        _seed_four_weeks(administrator, tenant)
        await _baselines(engine, store, tenant)
        span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
        slow = {ONSET + step * MINUTE: PAST_THE_MINIMUM for step in range(SUSTAIN)}
        _seed(administrator, tenant, span, slower_by=slow)

        async def refused(*_: object, **__: object) -> None:
            raise RuntimeError("the jobs insert was refused")

        monkeypatch.setattr(queue, "enqueue", refused)
        with pytest.raises(RuntimeError, match="jobs insert was refused"):
            await _judge_through(
                engine, store, tenant, _span(ONSET, ONSET + timedelta(minutes=SUSTAIN + 2))
            )
        monkeypatch.undo()

        findings, _ = await _projection(engine, tenant)
        assert findings == []
        assert project_job.OPENING_READS
    finally:
        await drop_tenant(engine, tenant)
