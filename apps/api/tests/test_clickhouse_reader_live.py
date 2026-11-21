"""The reader account can read, and nothing else (docs/changes/clickhouse-protects-itself).

The API's screens and the scheduler's sweeps connect as `nexovue_reader`
(`deploy/clickhouse/users.d/reader.xml`). Before it, every service held every
grant, and nothing outside the code bounded a read. These are refusals the
*server* makes, whatever a query sends.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from clickhouse_connect.driver.exceptions import DatabaseError
from live import require_stack
from nexovue_platform.tenant_statements import MAX_MEMORY_BYTES, READ_SETTINGS

ADMIN_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
PROBE_TABLE: Final = "reader_probe"
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)


def _reachable() -> bool:
    import socket

    parts = urlsplit(READER_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 1.0):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_clickhouse_reader_live.py",
    remedy=f"ClickHouse is not answering on {READER_DSN} — start it with `just dev`",
)


async def _connect(dsn: str) -> AsyncClient:
    parts = urlsplit(dsn)
    return await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )


@pytest_asyncio.fixture
async def probe_table() -> AsyncIterator[str]:
    """A throwaway table the admin owns, so a refused DROP is proved on something
    that exists — never on a real table, whose drop would not be a test."""
    admin = await _connect(ADMIN_DSN)
    try:
        await admin.command(f"CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (x UInt8) ENGINE = Memory")
        yield PROBE_TABLE
    finally:
        await admin.command(f"DROP TABLE IF EXISTS {PROBE_TABLE}")
        await admin.close()


@pytest_asyncio.fixture
async def reader() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(READER_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def test_the_reader_reads_with_every_bound_in_place(reader: AsyncClient) -> None:
    result = await reader.query(
        "SELECT count() > -1, getSetting('max_memory_usage') FROM spans", settings=READ_SETTINGS
    )
    readable, memory = result.result_rows[0]
    assert readable
    assert int(memory) == MAX_MEMORY_BYTES


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO spans (tenant_id) VALUES ('reader-probe')",
        "ALTER TABLE spans DELETE WHERE tenant_id = 'reader-probe'",
        "CREATE TABLE spans_reader_probe (x UInt8) ENGINE = Memory",
        "SELECT * FROM url('http://example.com', CSV, 'x String')",
    ],
)
async def test_the_reader_cannot_write_drop_create_or_reach_out(
    reader: AsyncClient, statement: str
) -> None:
    with pytest.raises(DatabaseError):
        await reader.command(statement)


@pytest.mark.parametrize("statement", ["DROP TABLE {table}", "TRUNCATE TABLE {table}"])
async def test_the_reader_cannot_drop_or_empty_a_table_that_exists(
    reader: AsyncClient, probe_table: str, statement: str
) -> None:
    with pytest.raises(DatabaseError):
        await reader.command(statement.format(table=probe_table))


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        # 0 means unlimited for both, which a maximum alone would let through.
        ("max_memory_usage", 0),
        ("max_memory_usage", MAX_MEMORY_BYTES * 4),
        ("max_execution_time", 0),
        ("max_execution_time", 3600),
    ],
)
async def test_a_read_cannot_lift_its_own_bounds(
    reader: AsyncClient, setting: str, value: int
) -> None:
    with pytest.raises(DatabaseError):
        await reader.query("SELECT 1", settings={setting: value})


# --------------------------------------------------- the API's own reader (F20, T5.6b)

API_READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_API_READER_DSN",
    "clickhouse://nexovue_api_reader:nexovue_api_reader_dev@localhost:18123/nexovue",
)
# The account's bounds, as `deploy/clickhouse/users.d/api-reader.xml` sets them.
API_READER_MEMORY_BYTES: Final = 2_576_980_378
API_READER_CONCURRENT_QUERIES: Final = 12
# Each read sleeps this long, so all of them are running at once when the last starts.
HELD_READ: Final = "SELECT sleepEachRow(0.5) FROM numbers(4)"


@pytest_asyncio.fixture
async def api_reader() -> AsyncIterator[AsyncClient]:
    client = await _connect(API_READER_DSN)
    try:
        yield client
    finally:
        await client.close()


async def test_the_api_reader_reads_with_its_per_user_bounds_in_place(
    api_reader: AsyncClient,
) -> None:
    result = await api_reader.query(
        "SELECT count() > -1, getSetting('max_memory_usage'), "
        "getSetting('max_memory_usage_for_user'), getSetting('max_concurrent_queries_for_user') "
        "FROM spans",
        settings=READ_SETTINGS,
    )
    readable, memory, memory_for_user, concurrent = result.result_rows[0]
    assert readable
    assert int(memory) == MAX_MEMORY_BYTES
    assert int(memory_for_user) == API_READER_MEMORY_BYTES
    assert int(concurrent) == API_READER_CONCURRENT_QUERIES


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_memory_usage_for_user", 0),
        ("max_memory_usage_for_user", API_READER_MEMORY_BYTES * 4),
        ("max_concurrent_queries_for_user", 0),
        ("max_concurrent_queries_for_user", 1_000),
        ("max_memory_usage", 0),
        ("max_execution_time", 3600),
    ],
)
async def test_an_api_read_cannot_lift_its_accounts_bounds(
    api_reader: AsyncClient, setting: str, value: int
) -> None:
    with pytest.raises(DatabaseError):
        await api_reader.query("SELECT 1", settings={setting: value})


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO spans (tenant_id) VALUES ('api-reader-probe')",
        "ALTER TABLE spans DELETE WHERE tenant_id = 'api-reader-probe'",
        "SELECT * FROM url('http://example.com', CSV, 'x String')",
    ],
)
async def test_the_api_reader_cannot_write_or_reach_out(
    api_reader: AsyncClient, statement: str
) -> None:
    with pytest.raises(DatabaseError):
        await api_reader.command(statement)


async def test_past_its_concurrent_reads_the_server_refuses_with_a_code_a_panel_reads_refused() -> (
    None
):
    """AC91's refusal half: one read past the account's 12 at once is refused by the
    server with TOO_MANY_SIMULTANEOUS_QUERIES, and `server_error_code` reads 202 off the
    driver's error, so a dashboard panel says `refused`, not `failed` (design section 6)."""
    import asyncio

    from nexovue_api.dashboardpanels.shared_reads import TOO_MANY_SIMULTANEOUS_QUERIES
    from nexovue_api.data.clickhouse.base import server_error_code

    clients = [await _connect(API_READER_DSN) for _ in range(API_READER_CONCURRENT_QUERIES + 3)]
    try:
        outcomes = await asyncio.gather(
            *(client.query(HELD_READ) for client in clients), return_exceptions=True
        )
    finally:
        for client in clients:
            await client.close()

    refused = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert refused, "every read ran: the account's concurrency bound did not hold"
    assert {server_error_code(error) for error in refused} == {TOO_MANY_SIMULTANEOUS_QUERIES}
    assert len(outcomes) - len(refused) <= API_READER_CONCURRENT_QUERIES
