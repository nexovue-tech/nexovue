"""The nightly erasure against real Postgres, ClickHouse and SeaweedFS (ADR-014).

**Three tenants.** Two deleted organizations and one live one, with rows in
every purged table and files under both upload prefixes. One run must erase the
deleted pair and leave the live one exactly as it was — the many-tenant
statement is the riskiest code in the deletion, and a live tenant beside it is
what proves it names only the owed.

**A record that lands late.** One deleted tenant gets new spans after the last
mutation finished, as a record already on the broker would. It must stay owed,
uncertified, for the next night — a certificate over rows still in the store is
the failure this run exists to prevent.

The run erases every organization owed in the dev database, not only these;
that is what it does in production, and an owed organization is owed.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import boto3
import clickhouse_connect
import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant
from botocore.config import Config
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.client import Client
from live import require_stack
from nexovue_platform import erasure
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.erasure import owed
from nexovue_scheduler.erasure.run import OLD_PARTS_LIFETIME_SECONDS, erase_owed
from nexovue_scheduler.erasure.store import ErasureStore
from nexovue_scheduler.erasure.tables import EXCLUDED_TABLES, PURGED_TABLES, TENANT_COLUMN
from nexovue_scheduler.probes.files import ObjectStoreFiles

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
S3_ENDPOINT: Final = os.environ.get("NEXOVUE_TEST_S3_ENDPOINT", "http://localhost:18333")
S3_BUCKET: Final = "nexovue-test-erasure"

ROWS_PER_TABLE: Final = 3
FILES_PER_PREFIX: Final = 2
WATERMARKS: Final = ("span_ingest_offsets", "span_written_offsets", "ingest_written_offsets")

# The three rollups partition and expire on `minute`; everything else on
# `received_at`. Both are now, so retention cannot remove a row mid-run.
_MINUTE_KEYED_TABLES: Final = frozenset({"endpoint_stats_1m", "service_edges_1m", "web_vitals_1m"})


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
    reachable=(
        _reachable(POSTGRES_DSN, 5432)
        and _reachable(CLICKHOUSE_DSN, 8123)
        and _reachable(S3_ENDPOINT, 8333)
    ),
    suite="the nightly erasure suite",
    remedy="Postgres, ClickHouse or SeaweedFS is not answering — start them with `just dev`",
)


@dataclass(frozen=True, slots=True)
class _S3Settings:
    """What `ObjectStoreFiles` reads; the client itself is handed in."""

    s3_bucket: str = S3_BUCKET


@pytest.fixture
def clickhouse() -> Iterator[Client]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=parts.path.lstrip("/"),
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def s3() -> Any:
    client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ.get("NEXOVUE_TEST_S3_KEY", "nexovue_dev_access"),
        aws_secret_access_key=os.environ.get("NEXOVUE_TEST_S3_SECRET", "nexovue_dev_secret"),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=S3_BUCKET)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=S3_BUCKET)
    return client


@pytest.fixture
async def three_tenants(
    engine: AsyncEngine, clickhouse: Client, s3: Any
) -> AsyncIterator[tuple[AlertTenant, AlertTenant, AlertTenant]]:
    """Two deleted organizations and a live one, each with telemetry and files."""
    tenants = (await make_tenant(engine), await make_tenant(engine), await make_tenant(engine))
    async with engine.begin() as connection:
        # The tenant fixture holds an enabled check, which the dev stack runs and
        # whose results would land mid-run. A deletion removes checks with the
        # request, and the live tenant's counts must not move, so none runs here.
        await connection.execute(
            text("DELETE FROM checks WHERE org_id = ANY(:orgs)"),
            {"orgs": [tenant.org_id for tenant in tenants]},
        )
        for tenant in tenants[:2]:
            await connection.execute(
                text("UPDATE organizations SET deleted_at = now() WHERE id = :id"),
                {"id": tenant.org_id},
            )
    for tenant in tenants:
        _write_telemetry(clickhouse, str(tenant.org_id))
        _write_files(s3, str(tenant.org_id))
    try:
        yield tenants
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM audit_log WHERE org_id IS NULL AND target = ANY(:targets)"),
                {"targets": [erasure.organization_target(t.org_id) for t in tenants]},
            )
        for tenant in tenants:
            await drop_tenant(engine, tenant)
            for prefix in erasure.tenant_prefixes(tenant.org_id):
                ObjectStoreFiles(_S3Settings(), client=s3).delete_prefix(prefix)  # type: ignore[arg-type]
            for table in PURGED_TABLES:
                delete_after_run(table, str(tenant.org_id))


class _RecordLandsLate(ErasureStore):
    """A record already on the broker, written after the last mutation finished."""

    def __init__(self, clickhouse: Client, org_id: str) -> None:
        super().__init__(clickhouse)
        self._clickhouse = clickhouse
        self._org_id = org_id

    def wait_for(self, table: str) -> None:
        super().wait_for(table)
        if table == PURGED_TABLES[-1]:
            _write_rows(self._clickhouse, "spans", self._org_id)


async def test_one_run_erases_the_deleted_and_leaves_the_live_untouched(
    engine: AsyncEngine,
    clickhouse: Client,
    s3: Any,
    three_tenants: tuple[AlertTenant, AlertTenant, AlertTenant],
) -> None:
    late, clean, live = three_tenants
    live_rows = _row_counts(clickhouse, str(live.org_id))
    live_files = _stored_keys(s3, str(live.org_id))
    watermarks = {table: _total_rows(clickhouse, table) for table in WATERMARKS}
    assert all(live_rows.values()) and len(live_files) == 2 * FILES_PER_PREFIX

    pauses: list[float] = []
    night = await erase_owed(
        engine,
        _RecordLandsLate(clickhouse, str(late.org_id)),
        ObjectStoreFiles(_S3Settings(), client=s3),  # type: ignore[arg-type]
        batch_size=1000,
        pause=pauses.append,
    )

    # The clean deleted tenant: no rows, no files, certified, no longer owed.
    assert str(clean.org_id) in night.erased
    assert not any(_row_counts(clickhouse, str(clean.org_id)).values())
    assert _stored_keys(s3, str(clean.org_id)) == []
    assert await _certified(engine, clean) == 1
    # The one a record reached late: still owed, with the table named.
    assert "spans" in night.still_owed[str(late.org_id)]
    assert await _certified(engine, late) == 0
    assert str(late.org_id) in (await _owed_now(engine)).ids
    # The live tenant, and the ingest watermarks, exactly as they were.
    assert _row_counts(clickhouse, str(live.org_id)) == live_rows
    assert _stored_keys(s3, str(live.org_id)) == live_files
    assert {table: _total_rows(clickhouse, table) for table in WATERMARKS} == watermarks
    # Organizations other suites delete meanwhile make a second group; each waits
    # out the first group's superseded parts.
    assert all(pause == OLD_PARTS_LIFETIME_SECONDS for pause in pauses)


async def test_an_erasure_owed_for_more_than_three_days_is_reported(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    now = datetime.now(UTC)
    before = await _overdue(engine, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE organizations SET deleted_at = :at WHERE id = :id"),
            {"at": now - owed.OVERDUE_AFTER - timedelta(hours=1), "id": tenant.org_id},
        )

    stuck = await _overdue(engine, now)

    assert stuck.count == before.count + 1
    assert stuck.oldest is not None and stuck.oldest <= now - owed.OVERDUE_AFTER
    async with engine.begin() as connection:
        await owed.certify(connection, str(tenant.org_id))
        try:
            assert (await owed.overdue(connection, now=now)).count == before.count
        finally:
            await connection.execute(
                text("DELETE FROM audit_log WHERE org_id IS NULL AND target = :target"),
                {"target": erasure.organization_target(tenant.org_id)},
            )


def test_the_running_server_holds_no_table_the_erasure_has_not_classified(
    clickhouse: Client,
) -> None:
    """The inventory against the deployed schema, not only the migrations: it
    catches a table somebody created by hand that no migration declares."""
    result = clickhouse.query(
        "SELECT t.name FROM system.tables AS t "
        "INNER JOIN system.columns AS c ON c.database = t.database AND c.table = t.name "
        "WHERE t.database = currentDatabase() AND c.name = {tenant_column:String} "
        "  AND t.engine NOT IN ('View', 'MaterializedView')",
        parameters={"tenant_column": TENANT_COLUMN},
    )
    on_the_server = {str(row[0]) for row in result.result_rows}

    unclassified = sorted(on_the_server - set(PURGED_TABLES) - set(EXCLUDED_TABLES))
    assert not unclassified, f"tables with {TENANT_COLUMN} the erasure ignores: {unclassified}"
    missing = sorted(set(PURGED_TABLES) - on_the_server)
    assert not missing, f"the erasure names tables the server does not have: {missing}"


# --- the parts ----------------------------------------------------------------------


def _write_telemetry(client: Client, tenant_id: str) -> None:
    for table in PURGED_TABLES:
        _write_rows(client, table, tenant_id)


def _write_rows(client: Client, table: str, tenant_id: str) -> None:
    """Rows differ by `project_id`, second in the aggregating tables' sort key,
    where identical rows would collapse to one."""
    written_at = datetime.now(UTC).replace(tzinfo=None)
    time_column = "minute" if table in _MINUTE_KEYED_TABLES else "received_at"
    columns = ["tenant_id", time_column]
    rows: list[list[Any]] = [[tenant_id, written_at] for _ in range(ROWS_PER_TABLE)]
    if table in _tables_with_a_project(client):
        columns.append("project_id")
        for index, row in enumerate(rows):
            row.append(f"erasure-fixture-{index}")
    client.insert(table, rows, column_names=columns)


def _tables_with_a_project(client: Client) -> frozenset[str]:
    result = client.query(
        "SELECT table FROM system.columns "
        "WHERE database = currentDatabase() AND name = 'project_id'"
    )
    return frozenset(str(row[0]) for row in result.result_rows)


def _row_counts(client: Client, tenant_id: str) -> dict[str, int]:
    """Counted outside the erasure's own statement, so the two cannot agree when
    both are wrong."""
    return {
        table: int(
            client.query(
                f"SELECT count() FROM {table} WHERE tenant_id = {{tenant_id:String}}",
                parameters={"tenant_id": tenant_id},
            ).result_rows[0][0]
        )
        for table in PURGED_TABLES
    }


def _total_rows(client: Client, table: str) -> int:
    return int(client.query(f"SELECT count() FROM {table}").result_rows[0][0])


def _write_files(s3: Any, tenant_id: str) -> None:
    for prefix in erasure.tenant_prefixes(tenant_id):
        for index in range(FILES_PER_PREFIX):
            s3.put_object(Bucket=S3_BUCKET, Key=f"{prefix}fixture/{index}", Body=b"{}")


def _stored_keys(s3: Any, tenant_id: str) -> list[str]:
    keys: list[str] = []
    for prefix in erasure.tenant_prefixes(tenant_id):
        listed = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        keys.extend(sorted(stored["Key"] for stored in listed.get("Contents", ())))
    return keys


async def _certified(engine: AsyncEngine, tenant: AlertTenant) -> int:
    async with engine.connect() as connection:
        found = await connection.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE org_id IS NULL "
                "AND action = :action AND target = :target"
            ),
            {"action": erasure.ERASED_ACTION, "target": erasure.organization_target(tenant.org_id)},
        )
        return int(found.scalar_one())


async def _owed_now(engine: AsyncEngine) -> owed.OwedTenants:
    async with engine.connect() as connection:
        return await owed.take_owed(connection, limit=10_000, attempted=frozenset())


async def _overdue(engine: AsyncEngine, now: datetime) -> owed.Overdue:
    async with engine.connect() as connection:
        return await owed.overdue(connection, now=now)
