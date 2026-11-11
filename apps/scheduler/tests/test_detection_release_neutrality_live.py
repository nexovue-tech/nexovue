"""Findings are unchanged by F28 (plan T3.9; AC42).

Two projects get the same four weeks and the same slowdown, minute for minute. One also
holds a release before the slowdown, from both doors: its rollup rows by version, the
release and its period, and a signed deploy. The project job judges both over the same
settled minutes. Their findings, readings and outbox rows must be equal, since nothing
detection reads holds a release, and FR44's line is composed at delivery and never
stored. `test_detection_isolation.py` holds the code paths; this holds the outcome.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import pytest
from alert_fixtures import (
    AlertTenant,
    add_rule,
    anomaly_rule,
    drop_tenant,
    make_tenant,
    rows,
)
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from detection_history import (
    CLICKHOUSE_DSN,
    ENDPOINT_ROLLUP,
    MINUTE,
    ONSET,
    PAST_THE_MINIMUM,
    POSTGRES_DSN,
    ROLLUP,
    SERVICE,
)
from detection_history import baselines_for as _baselines
from detection_history import judge_through as _judge_through
from detection_history import seed as _seed
from detection_history import seed_four_weeks as _seed_four_weeks
from detection_history import span as _span
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection.store import READINGS_TABLE, DetectionStore

VERSIONS = "service_versions_1m"
RELEASED_AT = ONSET - 10 * MINUTE


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
    suite="apps/scheduler/tests/test_detection_release_neutrality_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


async def _judged_project(engine: AsyncEngine) -> AlertTenant:
    made = await make_tenant(engine, with_check=False)
    for table in (ROLLUP, ENDPOINT_ROLLUP, READINGS_TABLE, VERSIONS):
        delete_after_run(table, str(made.org_id))
    async with engine.begin() as connection:
        await add_rule(
            connection,
            made.org_id,
            made.project_id,
            name="Findings",
            condition=anomaly_rule(),
            channel_ids=[made.owners_channel_id],
        )
    return made


@pytest.fixture
async def plain(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await _judged_project(engine)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


@pytest.fixture
async def released(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await _judged_project(engine)
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def _add_release(engine: AsyncEngine, client: Client, tenant: AlertTenant) -> None:
    """v2 shipped 10 minutes before the slowdown: reported, counted, and its period."""
    keys = {
        "org_id": tenant.org_id,
        "project_id": tenant.project_id,
        "service": SERVICE,
        "at": RELEASED_AT,
    }
    async with engine.begin() as connection:
        release_id = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version, first_seen_at,
                                      last_seen_at, first_reported_at, last_reported_at,
                                      report_count, last_state, last_activity_at)
                VALUES (:org_id, :project_id, :service, 'v2', :at, :at, :at, :at, 1,
                        'finished', now())
                RETURNING id
                """
            ),
            keys,
        )
        await connection.execute(
            text(
                """
                INSERT INTO release_periods (org_id, release_id, project_id, service_name,
                                             started_at, start_basis, previous_version,
                                             already_running, is_return)
                VALUES (:org_id, :release_id, :project_id, :service, :at, 'seen', 'v1',
                        false, false)
                """
            ),
            {**keys, "release_id": release_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO deploy_events (org_id, project_id, version, service_name,
                                           deployed_at, source, delivery_id, metadata, state,
                                           release_id)
                VALUES (:org_id, :project_id, 'v2', :service, :at, 'ci', 'neutrality-v2',
                        '{}', 'finished', :release_id)
                """
            ),
            {**keys, "release_id": release_id},
        )
    minutes = _span(RELEASED_AT, ONSET + 40 * MINUTE)
    values = ",".join(
        f"('{tenant.org_id}','{tenant.project_id}','{SERVICE}',{int(minute.timestamp())},'v2')"
        for minute in minutes
    )
    client.command(
        f"""
        INSERT INTO {VERSIONS}
            (tenant_id, project_id, service_name, minute, version, request_count, error_count,
             latency_buckets)
        SELECT tenant_id, project_id, service_name, toDateTime(epoch), version, 100, 0,
               arrayReduce('sumForEachState', [arrayWithConstant(64, toUInt32(0))])
        FROM values('tenant_id String, project_id String, service_name String,
                     epoch UInt32, version String', {values})
        """
    )


async def _judge(
    engine: AsyncEngine, store: DetectionStore, client: Client, tenant: AlertTenant
) -> None:
    _seed_four_weeks(client, tenant)
    span = _span(ONSET - timedelta(minutes=30), ONSET + timedelta(minutes=40))
    _seed(client, tenant, span, slower_by={ONSET + s * MINUTE: PAST_THE_MINIMUM for s in range(20)})
    await _baselines(engine, store, tenant)
    await _judge_through(
        engine, store, tenant, _span(ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=35))
    )


async def _outcome(engine: AsyncEngine, client: Client, tenant: AlertTenant) -> dict[str, Any]:
    """What detection wrote, without the ids and tenancy that differ by construction."""
    findings = await rows(
        engine,
        "SELECT scope, service_name, severity, state, closed_reason, title, signals, "
        "started_at, closed_at, muted, flapping FROM findings "
        "WHERE org_id = :org_id ORDER BY started_at",
        org_id=tenant.org_id,
    )
    outbox = await rows(
        engine,
        "SELECT kind, severity, state, sequence, attempts FROM notifications "
        "WHERE org_id = :org_id ORDER BY created_at, kind",
        org_id=tenant.org_id,
    )
    readings = client.query(
        # `version` is the row's write stamp, not what it says.
        f"SELECT * EXCEPT (tenant_id, project_id, version) FROM {READINGS_TABLE} FINAL "
        "WHERE tenant_id = {tenant:String} ORDER BY ALL",
        parameters={"tenant": str(tenant.org_id)},
    ).result_rows
    return {
        "findings": [tuple(row) for row in findings],
        "outbox": [tuple(row) for row in outbox],
        "readings": readings,
    }


async def test_a_release_before_a_slowdown_changes_nothing_detection_writes(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    plain: AlertTenant,
    released: AlertTenant,
) -> None:
    """AC42: the same seeded minutes, with and without release and deploy rows."""
    await _add_release(engine, administrator, released)

    await _judge(engine, store, administrator, plain)
    await _judge(engine, store, administrator, released)

    without, with_release = (
        await _outcome(engine, administrator, plain),
        await _outcome(engine, administrator, released),
    )
    # The harness opened something to compare: one finding, its notice, its readings.
    assert len(without["findings"]) == 1 and without["outbox"] and without["readings"]
    assert with_release == without
