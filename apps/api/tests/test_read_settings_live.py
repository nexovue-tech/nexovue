"""The settings every tenant read sends actually reach ClickHouse (blocker 14).

`READ_SETTINGS` is the one place a read's bounds are declared, and the
repository attaches it to every tenant read. This asks the server what it
received: a cap the server never sees is no cap.

Not a memory-exhaustion run: the development ClickHouse idles near the limit
of its 4 GiB, so a query big enough to trip a 1 GiB cap would, uncapped, push
the whole server over and fail unrelated queries. ClickHouse enforcing
`max_memory_usage` is its documented behaviour; ours to prove is that every
read asks for it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack
from nexovue_platform.tenant_statements import (
    MAX_EXTERNAL_GROUP_BY_BYTES,
    MAX_MEMORY_BYTES,
    READ_SETTINGS,
)

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)


def _reachable() -> bool:
    import socket

    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 1.0):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_read_settings_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`",
)


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
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


async def test_every_read_reaches_the_server_bounded_in_memory(clickhouse: AsyncClient) -> None:
    result = await clickhouse.query(
        "SELECT getSetting('max_memory_usage'), getSetting('max_bytes_before_external_group_by')",
        settings=READ_SETTINGS,
    )
    memory, external_group_by = result.result_rows[0]

    assert int(memory) == MAX_MEMORY_BYTES, (
        f"the server ran a read with max_memory_usage = {memory}: an unbounded read can take "
        "the shared ClickHouse to its ceiling, where every query fails — ingest included"
    )
    assert int(external_group_by) == MAX_EXTERNAL_GROUP_BY_BYTES
