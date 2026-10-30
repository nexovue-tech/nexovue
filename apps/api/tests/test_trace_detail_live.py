"""A trace opened against the running stack shows each span once.

`spans` can hold one span twice: a replay whose watermark write was lost stores
it again (`apps/workers/internal/pipeline/ingeststate/state.go`), and nothing in
the table's sort key collapses the copy. Only the server can show the statement
keeps one, so this writes a trace with a repeated span and opens it.

It fails when the stack is not up, because a skip here is indistinguishable
from a pass. It writes under a tenant of its own and deletes what it wrote.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.traces import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
PROJECT: Final = "detail-probe"
TRACE: Final = "5f1c0de5" * 4
ROOT: Final = "a0a0a0a0a0a0a0a0"
REPLAYED: Final = "b1b1b1b1b1b1b1b1"


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 1.0):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="the live trace detail suite",
    remedy="ClickHouse is not running — start the dev stack with `just dev`",
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


@pytest_asyncio.fixture
async def replayed_trace(clickhouse: AsyncClient) -> AsyncIterator[str]:
    """A root and one child, the child stored twice as a lost watermark leaves it."""
    tenant_id = str(uuid.uuid4())
    started = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    received = started + timedelta(seconds=2)
    span_columns = [
        "tenant_id", "project_id", "trace_id", "span_id", "parent_span_id",
        "service_name", "span_name", "timestamp", "received_at", "duration_ms",
    ]  # fmt: skip
    root = [tenant_id, PROJECT, TRACE, ROOT, "", "frontend", "checkout", started, received, 20.0]
    child = [
        tenant_id, PROJECT, TRACE, REPLAYED, ROOT,
        "flagd", "ResolveAll", started + timedelta(milliseconds=5), received, 1.0,
    ]  # fmt: skip
    await clickhouse.insert("spans", [root, child, child], column_names=span_columns)
    await clickhouse.insert(
        "span_trace_lookup",
        [[tenant_id, TRACE, started, started + timedelta(milliseconds=20), received]],
        column_names=["tenant_id", "trace_id", "span_start", "span_end", "received_at"],
    )
    try:
        yield tenant_id
    finally:
        for table in ("spans", "span_trace_lookup"):
            await clickhouse.command(
                f"DELETE FROM {table} WHERE tenant_id = {{tenant_id:String}}",
                parameters={"tenant_id": tenant_id},
            )


async def test_a_replayed_span_is_shown_once(clickhouse: AsyncClient, replayed_trace: str) -> None:
    detail = await service.get_trace(
        TenantScopedRepository(clickhouse, tenant_id=replayed_trace),
        project_id=PROJECT,
        trace_id=TRACE,
    )

    assert sorted(span.span_id for span in detail.spans) == [ROOT, REPLAYED]
