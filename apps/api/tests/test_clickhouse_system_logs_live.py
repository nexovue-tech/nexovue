"""ClickHouse's own logs are bounded (`docs/changes/clickhouse-system-logs/`).

Every system log is enabled by default with no TTL, and each is an ordinary
MergeTree: it inserts, merges and holds part metadata in memory for ever. On the
development stack that reached 6.7 GiB of the 7.3 GiB on disk — against ~600 MiB
of actual customer telemetry — inside a server capped at 4 GiB, which is why
unrelated live suites failed `MEMORY_LIMIT_EXCEEDED`.

This reads the store's own catalogue rather than the configuration file: what
matters is what the running server did with it. A configuration that is mounted
but ignored looks exactly like one that is absent, and only this tells them
apart.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from clickhouse_connect.driver import AsyncClient, create_async_client
from live import require_stack

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

# Off: each is ClickHouse describing itself, and nothing here reads any of them.
SILENCED: Final = (
    "text_log",
    "trace_log",
    "processors_profile_log",
    "asynchronous_metric_log",
)

# Kept, because an operator reads them during an incident — and bounded, because
# the two most useful tables must not be the two hardest to query.
BOUNDED: Final = ("query_log", "part_log", "metric_log")


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="apps/api/tests/test_clickhouse_system_logs_live.py",
    remedy="ClickHouse is not reachable — run `just up`",
)


@pytest.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await create_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "default",
        password=parts.password or "",
        database=(parts.path or "/nexovue").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


async def _system_tables(clickhouse: AsyncClient) -> dict[str, str]:
    """Every table in the `system` database, with the TTL it was created with."""
    result = await clickhouse.query(
        "SELECT name, create_table_query FROM system.tables WHERE database = 'system'"
    )
    # Typed on the way out: the driver hands back `Sequence[Sequence[Any]]`.
    return {str(name): str(query) for name, query in result.result_rows}


@pytest.mark.parametrize("table", SILENCED)
async def test_a_log_nothing_reads_does_not_exist(clickhouse: AsyncClient, table: str) -> None:
    """`remove="1"` in `deploy/clickhouse/config.d/system-logs.xml`. An existing
    store keeps the table until it is dropped by hand; a fresh one never has it."""
    assert table not in await _system_tables(clickhouse), (
        f"system.{table} is still being written. On a store that predates this "
        f"configuration: DROP TABLE system.{table}."
    )


@pytest.mark.parametrize("table", BOUNDED)
async def test_a_log_an_operator_reads_cannot_grow_without_bound(
    clickhouse: AsyncClient, table: str
) -> None:
    tables = await _system_tables(clickhouse)

    assert table in tables, f"system.{table} is gone; an operator needs it during an incident"
    assert "TTL event_date" in tables[table], (
        f"system.{table} has no TTL, so it grows for ever. On a store that predates "
        f"this configuration: ALTER TABLE system.{table} MODIFY TTL event_date + INTERVAL 3 DAY."
    )
