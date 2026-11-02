"""Detection's store against real ClickHouse and Postgres (ADR-023, design 2.1 item 5; plan T2b.2).

Written as the writer account and read as the reader, exactly as the project job
will. This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection.store import (
    READINGS_TABLE,
    DetectionStore,
    ProjectBinding,
    Reading,
    bind_project,
    client_at,
    connect,
)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
# The API's account, shared with the alert sweeps (users.d/reader.xml).
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
SERVICE: Final = "checkout"


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
    suite="apps/scheduler/tests/test_detection_store_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


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


def _minute() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=3)


def _reading(minute: datetime, **changes: object) -> Reading:
    reading = Reading(
        service_name=SERVICE,
        minute=minute,
        window_minutes=10,
        window_requests=1_200,
        window_errors=6,
        window_p95_ms=245.0,
        window_above_threshold=40,
        endpoint_failures={"POST /api/checkout": (300, 5)},
        traffic_mode="weekday",
        traffic_days=21,
        traffic_median=11.0,
        traffic_spread=0.8,
        traffic_verdict="normal",
        failing_mode="weekday",
        failing_days=21,
        failing_median=0.004,
        failing_spread=0.001,
        failing_burst_height=0.02,
        failing_burst_minutes=3,
        failing_verdict="normal",
        slower_mode="weekday",
        slower_days=21,
        slower_median=210.0,
        slower_spread=12.0,
        slower_threshold_ms=320.0,
        slower_verdict="normal",
    )
    return replace(reading, **changes)  # type: ignore[arg-type]


def _binding(org_id: str, project_id: str) -> ProjectBinding:
    delete_after_run(READINGS_TABLE, org_id)
    return ProjectBinding(org_id=org_id, project_id=project_id, derived_retention_days=30)


async def test_the_binding_takes_the_organization_from_the_project_row(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    async with engine.connect() as connection:
        bound = await bind_project(connection, tenant.project_id)
        unknown = await bind_project(connection, uuid.uuid4())

    assert bound is not None
    assert bound.org_id == str(tenant.org_id)
    assert bound.project_id == str(tenant.project_id)
    assert bound.derived_retention_days > 0
    assert unknown is None, "a project that does not exist bound to something"


async def test_written_as_one_project_nothing_is_found_as_another(
    engine: AsyncEngine, store: DetectionStore
) -> None:
    first, second = (
        await make_tenant(engine, with_check=False),
        await make_tenant(engine, with_check=False),
    )
    try:
        async with engine.connect() as connection:
            a = await bind_project(connection, first.project_id)
            b = await bind_project(connection, second.project_id)
        assert a is not None and b is not None
        delete_after_run(READINGS_TABLE, a.org_id)
        minute = _minute()

        store.for_project(a).write([_reading(minute)], version=1)

        window = {"start": minute - timedelta(minutes=1), "end": minute + timedelta(minutes=1)}
        same_tenant_other_project = replace(a, project_id=b.project_id)
        other_tenant_same_project = replace(b, project_id=a.project_id)
        assert [r.service_name for r in store.for_project(a).readings(**window)] == [SERVICE]
        assert store.for_project(b).readings(**window) == []
        assert store.for_project(same_tenant_other_project).readings(**window) == []
        assert store.for_project(other_tenant_same_project).readings(**window) == []
    finally:
        await drop_tenant(engine, first)
        await drop_tenant(engine, second)


def test_the_same_service_in_two_tenants_keeps_both_readings_through_a_merge(
    store: DetectionStore, administrator: Client
) -> None:
    project = str(uuid.uuid4())
    tenants = [str(uuid.uuid4()), str(uuid.uuid4())]
    minute = _minute()
    for tenant in tenants:
        store.for_project(_binding(tenant, project)).write([_reading(minute)], version=1)

    administrator.command(f"OPTIMIZE TABLE {READINGS_TABLE} FINAL")

    kept = administrator.query(
        f"SELECT tenant_id FROM {READINGS_TABLE} "
        "WHERE project_id = {project:String} AND service_name = {service:String} "
        "AND minute = {minute:DateTime}",
        parameters={"project": project, "service": SERVICE, "minute": minute},
    ).result_rows
    assert sorted(row[0] for row in kept) == sorted(tenants), (
        "a replacing merge kept one tenant's reading and dropped the other's"
    )


def test_a_minute_written_again_is_read_once_as_its_newest_version(
    store: DetectionStore,
) -> None:
    detection = store.for_project(_binding(str(uuid.uuid4()), str(uuid.uuid4())))
    minute = _minute()
    detection.write([_reading(minute)], version=1)

    # The newest version has no p95: it must not be filled in from the older one.
    detection.write([_reading(minute, window_p95_ms=None, slower_verdict="learning")], version=2)

    readings = detection.readings(start=minute, end=minute + timedelta(minutes=1))
    assert len(readings) == 1
    assert readings[0].window_p95_ms is None
    assert readings[0].slower_verdict == "learning"
    assert readings[0] == _reading(minute, window_p95_ms=None, slower_verdict="learning")


def test_the_api_s_account_is_refused_an_insert() -> None:
    reader = client_at(READER_DSN)
    tenant = str(uuid.uuid4())
    delete_after_run(READINGS_TABLE, tenant)

    with pytest.raises(DatabaseError, match=r"READONLY|ACCESS_DENIED|readonly"):
        reader.insert(
            READINGS_TABLE,
            [(tenant, "project", SERVICE, _minute())],
            column_names=["tenant_id", "project_id", "service_name", "minute"],
        )


@pytest.mark.parametrize(
    "statement",
    [
        f"SELECT count() FROM {READINGS_TABLE}",
        "SELECT count() FROM spans",
        "INSERT INTO service_stats_1m (tenant_id) VALUES ('writer-must-not-write')",
        f"ALTER TABLE {READINGS_TABLE} DELETE WHERE 1",
    ],
)
def test_the_writer_can_do_nothing_but_insert_readings(statement: str) -> None:
    writer = client_at(WRITER_DSN)

    with pytest.raises(DatabaseError, match="ACCESS_DENIED"):
        writer.command(statement)


def test_the_writer_cannot_raise_its_own_bounds() -> None:
    writer = client_at(WRITER_DSN)

    with pytest.raises(DatabaseError, match="SETTING_CONSTRAINT_VIOLATION"):
        writer.command("SELECT 1", settings={"max_execution_time": 3600})
