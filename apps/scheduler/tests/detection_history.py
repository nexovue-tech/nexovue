"""History and judging for the live detection suites: one service's minutes written
straight into `service_stats_1m` with the store's own account, the hourly baseline
job run over them, and the project job run minute by minute, as the coordinator
hands it settled minutes. Shared by `test_detection_project_job_live.py` and
`test_detection_shifts_live.py`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection import baselines as baseline_job
from nexovue_scheduler.detection.method import BUCKET_COUNT
from nexovue_scheduler.detection.project_job import Outcome, judge_project
from nexovue_scheduler.detection.store import (
    READINGS_TABLE,
    DetectionStore,
    client_at,
    connect,
)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
WRITER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DETECTION_WRITER_DSN",
    "clickhouse://nexovue_detection_writer:nexovue_detection_writer_dev@localhost:18123/nexovue",
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ROLLUP: Final = "service_stats_1m"
ENDPOINT_ROLLUP: Final = "endpoint_stats_1m"
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
# Normal sensitivity, the default: a 5-minute window, so 5 minutes of sustain.
SUSTAIN: Final = 5
# Time to a finding beyond critical, at normal sensitivity (spec, *Product values*).
TIME_TO_A_FINDING: Final = timedelta(minutes=12)
# The minute the trouble starts: noon today, far from any midnight a pool stops at.
ONSET: Final = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
# A steady latency shape over 11 buckets, in percent, at 100 requests a minute.
SHAPE: Final = (1, 3, 8, 15, 22, 20, 14, 9, 5, 2, 1)
FIRST_BUCKET: Final = 20
REQUESTS: Final = 100
# Buckets are about 1.19x wide: 5 is about 2.4x slower, 10 about 5.6x, past critical.
PAST_THE_MINIMUM: Final = 5
PAST_CRITICAL: Final = 10
INSERT_BATCH: Final = 400


@pytest.fixture
def store() -> DetectionStore:
    return connect(READER_DSN, WRITER_DSN)


@pytest.fixture
def administrator() -> Iterator[Client]:
    client = client_at(CLICKHOUSE_DSN)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
async def project(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    delete_after_run(ROLLUP, str(made.org_id))
    delete_after_run(ENDPOINT_ROLLUP, str(made.org_id))
    delete_after_run(READINGS_TABLE, str(made.org_id))
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


# ------------------------------------------------------------------ seeding


def latency_buckets(slower_by: int, requests: int = REQUESTS) -> list[int]:
    buckets = [0] * BUCKET_COUNT
    for offset, percent in enumerate(SHAPE):
        buckets[FIRST_BUCKET + offset + slower_by] += requests * percent // 100
    buckets[FIRST_BUCKET + 4 + slower_by] += requests - sum(buckets)
    return buckets


def seed_endpoints(
    client: Client,
    tenant: AlertTenant,
    service: str,
    endpoints: Mapping[datetime, Mapping[str, tuple[int, int]]],
) -> None:
    rows = [
        f"('{tenant.org_id}','{tenant.project_id}','{service}','SERVER','{name}',"
        f"{int(minute.timestamp())},{requests},{errors})"
        for minute, counts in endpoints.items()
        for name, (requests, errors) in counts.items()
    ]
    for start in range(0, len(rows), INSERT_BATCH):
        client.command(
            f"""
            INSERT INTO {ENDPOINT_ROLLUP}
                (tenant_id, project_id, service_name, span_kind, span_name, minute,
                 request_count, error_count)
            SELECT tenant_id, project_id, service_name, span_kind, span_name,
                   toDateTime(epoch), requests, errors
            FROM values(
                'tenant_id String, project_id String, service_name String, span_kind String,
                 span_name String, epoch UInt32, requests UInt64, errors UInt64',
                {",".join(rows[start : start + INSERT_BATCH])}
            )
            """
        )


def seed(
    client: Client,
    tenant: AlertTenant,
    minutes: Sequence[datetime],
    *,
    service: str = SERVICE,
    slower_by: dict[datetime, int] | None = None,
    requests: int = REQUESTS,
    errors: int = 0,
    endpoints: Mapping[datetime, Mapping[str, tuple[int, int]]] | None = None,
) -> None:
    """Rows as the worker writes them: the buckets folded into their aggregate state,
    and each endpoint's counts in its own rollup."""
    rows = []
    for minute in minutes:
        buckets = latency_buckets((slower_by or {}).get(minute, 0), requests)
        failures = errors
        if endpoints is not None and minute in endpoints:
            failures = sum(e for _, e in endpoints[minute].values())
        rows.append(
            f"('{tenant.org_id}','{tenant.project_id}','{service}',"
            f"{int(minute.timestamp())},{sum(buckets)},{failures},{buckets})"
        )
    if endpoints is not None:
        seed_endpoints(client, tenant, service, endpoints)
    for start in range(0, len(rows), INSERT_BATCH):
        client.command(
            f"""
            INSERT INTO {ROLLUP}
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values(
                'tenant_id String, project_id String, service_name String, epoch UInt32,
                 requests UInt64, errors UInt64, buckets Array(UInt32)',
                {",".join(rows[start : start + INSERT_BATCH])}
            )
            """
        )


def span(first: datetime, last: datetime) -> list[datetime]:
    return [first + step * MINUTE for step in range((last - first) // MINUTE + 1)]


def seed_four_weeks(
    client: Client,
    tenant: AlertTenant,
    *,
    service: str = SERVICE,
    errors: int = 0,
    endpoints: Mapping[str, tuple[int, int]] | None = None,
    until: timedelta = timedelta(minutes=240),
) -> None:
    """A service first seen 29 days ago, with normal traffic around noon on the four
    same weekdays its weekly pool reads; and its endpoints, when given."""
    seed(client, tenant, [ONSET - timedelta(days=29)], service=service)
    for weeks in range(1, 5):
        day = ONSET - timedelta(days=7 * weeks)
        minutes = span(day - timedelta(minutes=200), day + until)
        seed(
            client,
            tenant,
            minutes,
            service=service,
            errors=errors,
            endpoints=None if endpoints is None else dict.fromkeys(minutes, endpoints),
        )


async def baselines_for(
    engine: AsyncEngine,
    store: DetectionStore,
    tenant: AlertTenant,
    *,
    now: datetime = ONSET - timedelta(minutes=5),
) -> None:
    written = await baseline_job.compute_project(engine, store, tenant.project_id, now=now)
    assert written is not None and written >= 1


async def judge_through(
    engine: AsyncEngine,
    store: DetectionStore,
    tenant: AlertTenant,
    minutes: Sequence[datetime],
) -> list[Outcome]:
    """The coordinator's minutes, one settled minute after another."""
    return [
        await judge_project(engine, store, tenant.project_id, settled=minute, now=minute)
        for minute in minutes
    ]


async def findings(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT * FROM findings WHERE org_id = :org_id AND project_id = :project_id "
                    "ORDER BY started_at"
                ),
                {"org_id": tenant.org_id, "project_id": tenant.project_id},
            )
        )


async def state(engine: AsyncEngine, tenant: AlertTenant, service: str = SERVICE) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT * FROM detection_state WHERE org_id = :org_id "
                    "AND project_id = :project_id AND service_name = :service"
                ),
                {"org_id": tenant.org_id, "project_id": tenant.project_id, "service": service},
            )
        ).one()
