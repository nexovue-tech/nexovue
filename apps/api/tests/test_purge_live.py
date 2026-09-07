"""The purge against the running ClickHouse, with two tenants (C6).

**Why two.** A purge that deletes too much is worse than one that deletes too
little, and a single-tenant fixture cannot tell those apart — an
`ALTER TABLE spans DELETE WHERE 1` passes it. So every table gets rows for two
tenants, one is purged, and the other is counted before and after and must be
identical.

**Why this is not flaky.** `ALTER TABLE ... DELETE` is a mutation: it returns
before the rows are gone, so asserting straight afterwards passes or fails at
random and reads as a purge defect. The suite waits on the condition itself —
`TenantPurge.wait_until_erased` polls the row counts until they are zero — and
never on a sleep.

**What it leaves behind.** Nothing. Both fixture tenants are purged on the way
out, including the one the test proves was untouched. Its rows are counted
first, so the assertion has already run.

This suite fails rather than skips when the stack is down, for the reason
`tests/helpers/live.py` gives.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedPurger
from nexovue_api.purge import PurgeUnfinishedError, TenantPurge
from nexovue_api.purge.tables import EXCLUDED_TABLES, PURGED_TABLES, TENANT_COLUMN

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Generous: a mutation over the dev store's parts is not instant, and a slow
# machine should fail on a real problem rather than on this number.
PURGE_DEADLINE_SECONDS: Final = 120
POLL_SECONDS: Final = 0.25

# Enough rows per table that "the other tenant is untouched" is a statement
# about a set rather than about one row.
ROWS_PER_TABLE: Final = 3

# The three rollups partition and expire on `minute`; everything else on
# `received_at`. Both are set to now, so the retention TTL cannot remove a
# fixture row mid-run and be mistaken for the purge working.
_MINUTE_KEYED_TABLES: Final = frozenset({"endpoint_stats_1m", "service_edges_1m", "web_vitals_1m"})


def _time_column(table: str) -> str:
    return "minute" if table in _MINUTE_KEYED_TABLES else "received_at"


def _reachable(dsn: str) -> bool:
    parsed = urlsplit(dsn)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 8123),
            timeout=REACHABILITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="the ClickHouse tenant purge suite",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN}; start it with `just dev`",
)


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parsed = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parsed.hostname or "localhost",
        port=parsed.port or 8123,
        username=parsed.username or "default",
        password=parsed.password or "",
        database=(parsed.path or "/default").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def _tables_with_a_project(client: AsyncClient) -> frozenset[str]:
    """Which purged tables carry `project_id`, read rather than remembered."""
    result = await client.query(
        "SELECT table FROM system.columns "
        "WHERE database = currentDatabase() AND name = 'project_id'"
    )
    return frozenset(str(row[0]) for row in result.result_rows)


async def _write_fixture_rows(client: AsyncClient, tenant_id: str) -> None:
    """One tenant's rows in every purged table, timestamped now.

    The rows of a table differ by `project_id`, which is second in the sort key
    of all four `AggregatingMergeTree` tables. Identical rows there collapse to
    one inside the inserted part, so a fixture of three would count as one and
    the suite would silently be testing a third of what it says it is.
    `span_trace_lookup` has no project and needs none: it is a plain MergeTree.
    """
    written_at = datetime.now(UTC).replace(tzinfo=None)
    has_project = await _tables_with_a_project(client)

    for table in PURGED_TABLES:
        columns = ["tenant_id", _time_column(table)]
        rows = [[tenant_id, written_at] for _ in range(ROWS_PER_TABLE)]
        if table in has_project:
            columns.append("project_id")
            for index, row in enumerate(rows):
                row.append(f"purge-fixture-{index}")
        await client.insert(table, rows, column_names=columns)


async def _row_counts(client: AsyncClient, tenant_id: str) -> dict[str, int]:
    """Rows per table for one tenant, read outside the purge's own statement.

    Deliberately not `TenantPurge.remaining()`: a check that used the code under
    test to prove the code under test would agree with it when both are wrong.
    """
    counts: dict[str, int] = {}
    for table in PURGED_TABLES:
        result = await client.query(
            f"SELECT count() FROM {table} WHERE tenant_id = {{tenant_id:String}}",
            parameters={"tenant_id": tenant_id},
        )
        counts[table] = int(result.result_rows[0][0])
    return counts


async def _purge_now(client: AsyncClient, tenant_id: str) -> None:
    purge = TenantPurge(TenantScopedPurger(client, tenant_id))
    await purge.run(deadline_seconds=PURGE_DEADLINE_SECONDS)


@pytest_asyncio.fixture
async def two_tenants(clickhouse: AsyncClient) -> AsyncIterator[tuple[str, str]]:
    """A tenant to delete and a tenant that must survive it.

    Fresh ids every run, so the suite can never collide with a real
    organization's rows in the shared dev store.
    """
    purged, spared = str(uuid.uuid4()), str(uuid.uuid4())
    await _write_fixture_rows(clickhouse, purged)
    await _write_fixture_rows(clickhouse, spared)
    try:
        yield purged, spared
    finally:
        for tenant_id in (purged, spared):
            await _purge_now(clickhouse, tenant_id)


async def test_the_fixture_writes_rows_this_suite_could_lose(
    clickhouse: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """A purge test over an empty store passes without deleting anything.

    Counts are asserted non-zero rather than equal to `ROWS_PER_TABLE`: three
    spans also make `span_trace_lookup_mv` write a row of its own, which is the
    cascade this package exists for and is left in the fixture on purpose. Both
    tenants must land the same rows, or "untouched" would be measuring noise.
    """
    purged, spared = two_tenants

    before_purged = await _row_counts(clickhouse, purged)
    before_spared = await _row_counts(clickhouse, spared)

    empty = sorted(table for table, count in before_purged.items() if not count)
    assert not empty, f"the fixture wrote nothing to these tables: {empty}"
    assert before_purged == before_spared, (
        f"the two tenants were not given the same rows: {before_purged} vs {before_spared}"
    )


async def test_the_purge_empties_every_table_for_the_named_tenant(
    clickhouse: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """The obligation itself: no purged table still holds the deleted tenant."""
    purged, _ = two_tenants
    before = await _row_counts(clickhouse, purged)
    assert all(before.values()), before

    await _purge_now(clickhouse, purged)

    after = await _row_counts(clickhouse, purged)
    left_behind = {table: count for table, count in after.items() if count}
    assert not left_behind, f"the purge left rows behind: {left_behind}"


async def test_the_purge_leaves_the_other_tenant_untouched(
    clickhouse: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """The mutation rewrites parts that hold both tenants; only one may go."""
    purged, spared = two_tenants
    before = await _row_counts(clickhouse, spared)

    await _purge_now(clickhouse, purged)

    after = await _row_counts(clickhouse, spared)
    assert after == before, (
        f"the purge of {purged} changed rows belonging to {spared}: before={before} after={after}"
    )


@pytest.mark.parametrize("omitted", ["span_trace_lookup", "trace_summaries"])
async def test_a_table_left_out_of_the_purge_is_named_rather_than_missed(
    clickhouse: AsyncClient,
    two_tenants: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    omitted: str,
) -> None:
    """The negative control, kept rather than run once by hand.

    `service.PURGED_TABLES` drives the deletions and `COUNT_REMAINING_ROWS`
    drives the proof, and the two are written separately. Dropping a table from
    the first must therefore be reported by the second, naming it — not pass
    quietly, which is exactly the defect this package exists to fix.

    `span_trace_lookup` is the case the plan names: a materialized view wrote
    those rows from `spans`, and a mutation on `spans` does not reach them.
    """
    purged, _ = two_tenants
    before = await _row_counts(clickhouse, purged)
    submitted = tuple(table for table in PURGED_TABLES if table != omitted)
    monkeypatch.setattr("nexovue_api.purge.service.PURGED_TABLES", submitted)

    purge = TenantPurge(TenantScopedPurger(clickhouse, purged))
    await purge.submit()
    await _wait_until_empty(clickhouse, purged, submitted)

    assert await purge.remaining() == {omitted: before[omitted]}

    with pytest.raises(PurgeUnfinishedError) as refused:
        await purge.wait_until_erased(deadline_seconds=0)
    assert refused.value.remaining == {omitted: before[omitted]}
    assert omitted in str(refused.value)


async def _wait_until_empty(client: AsyncClient, tenant_id: str, tables: tuple[str, ...]) -> None:
    """Poll the named tables until the tenant is gone from all of them.

    The suite's own wait, so a test of the purge's waiting is not measured by
    the purge's waiting.
    """
    deadline = asyncio.get_running_loop().time() + PURGE_DEADLINE_SECONDS
    while True:
        counts = await _row_counts(client, tenant_id)
        left = {table: counts[table] for table in tables if counts[table]}
        if not left:
            return
        assert asyncio.get_running_loop().time() < deadline, f"still holding rows: {left}"
        await asyncio.sleep(POLL_SECONDS)


async def test_the_watermark_tables_are_untouched_by_a_purge(
    clickhouse: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """Deleting from them causes a silent, permanent over-count (ADR 0094).

    They have no tenant column, so the only way a purge could reach them is by
    someone adding them to the inventory. Counted across the whole table,
    because there is no tenant to count within.
    """
    purged, _ = two_tenants
    watermarks = ("span_ingest_offsets", "span_written_offsets", "ingest_written_offsets")
    before = {table: await _total_rows(clickhouse, table) for table in watermarks}

    await _purge_now(clickhouse, purged)

    after = {table: await _total_rows(clickhouse, table) for table in watermarks}
    assert after == before, f"a purge moved an ingest watermark: before={before} after={after}"


async def _total_rows(client: AsyncClient, table: str) -> int:
    result = await client.query(f"SELECT count() FROM {table}")
    return int(result.result_rows[0][0])


async def test_the_running_server_holds_no_table_this_purge_has_not_classified(
    clickhouse: AsyncClient,
) -> None:
    """The inventory against the server, not only against the migrations.

    `test_purge_inventory.py` reads `deploy/clickhouse/migrations`, which is the
    schema as written. This asks the schema as deployed, and catches what a file
    cannot: a materialized view with inner storage, or a table somebody created
    by hand and the migrations never learned about.

    Views are excluded because they hold no rows of their own — an inner table
    would appear here separately, under its own `.inner_id.` name.
    """
    result = await clickhouse.query(
        "SELECT t.name FROM system.tables AS t "
        "INNER JOIN system.columns AS c ON c.database = t.database AND c.table = t.name "
        "WHERE t.database = currentDatabase() AND c.name = {tenant_column:String} "
        "  AND t.engine NOT IN ('View', 'MaterializedView')",
        parameters={"tenant_column": TENANT_COLUMN},
    )
    on_the_server = {str(row[0]) for row in result.result_rows}

    unclassified = sorted(on_the_server - set(PURGED_TABLES) - set(EXCLUDED_TABLES))
    assert not unclassified, (
        f"the server holds these tables with a {TENANT_COLUMN} column and the purge "
        f"neither deletes from them nor records why not: {unclassified}"
    )
    missing = sorted(set(PURGED_TABLES) - on_the_server)
    assert not missing, f"the purge names tables the server does not have: {missing}"
