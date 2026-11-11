"""A dependency fact's traces link, run against the real trace search (F3 plan T2.4;
AC105's live cases, A76).

`evidence-links.fixture.json` holds each callee shape a dependency fact can have, the
search its link carries, and which traces that search must find. The console's test
builds each search from it; this one runs each search's filters through
`traces.service.list_traces` against ClickHouse, over traces seeded for the shape:

- a callee that reports for itself, found by `service` alone;
- one only its caller named, by each of `service.peer.name`, `peer.service` and
  `server.address`, found by the caller's span carrying that attribute;
- one named by nothing that can match, which opens the caller's failing calls and says
  they are not narrowed.

It writes its own tenant's rows and deletes them; it fails when ClickHouse is not up.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.traces import service as service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
FIXTURE: Final = Path(__file__).resolve().parents[2] / "web/src/shared/evidence-links.fixture.json"
PROJECT: Final = str(uuid.uuid4())
WRITTEN: Final = ("spans", "trace_summaries")


def _reachable(dsn: str) -> bool:
    parts = urlsplit(dsn)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 8123), 1.0):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN),
    suite="apps/api/tests/test_evidence_link_traces_live.py",
    remedy="ClickHouse is not running — start the dev stack with `just dev`",
)

CASES: Final[list[dict[str, Any]]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

# Each seeded trace: its spans as (service, kind, status, duration in ms, attributes).
TRACES: Final[dict[str, list[tuple[str, str, str, float, dict[str, str]]]]] = {
    "through-payments": [
        ("checkout", "SERVER", "OK", 50.0, {}),
        ("payments", "SERVER", "ERROR", 30.0, {}),
    ],
    "through-payments-slow": [
        ("checkout", "SERVER", "OK", 500.0, {}),
        ("payments", "SERVER", "OK", 480.0, {}),
    ],
    "named-by-service-peer-name": [
        ("checkout", "SERVER", "OK", 40.0, {}),
        ("checkout", "CLIENT", "ERROR", 30.0, {"service.peer.name": "fraud"}),
    ],
    "named-by-peer-service": [
        ("checkout", "SERVER", "OK", 40.0, {}),
        ("checkout", "CLIENT", "ERROR", 30.0, {"peer.service": "ledger"}),
    ],
    "named-by-server-address": [
        ("checkout", "SERVER", "OK", 40.0, {}),
        ("checkout", "CLIENT", "ERROR", 30.0, {"server.address": "db.internal"}),
    ],
    "unnamed-callee": [
        ("checkout", "SERVER", "OK", 40.0, {}),
        ("checkout", "CLIENT", "ERROR", 30.0, {}),
    ],
}


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[tuple[AsyncClient, str, dict[str, str], datetime]]:
    """One tenant's traces, one per shape; the trace ids by name."""
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    tenant_id = str(uuid.uuid4())
    started = datetime.now(UTC).replace(tzinfo=None, microsecond=0) - timedelta(minutes=20)
    ids = {name: uuid.uuid4().hex for name in TRACES}
    span_rows, summary_rows = [], []
    for offset, (name, spans) in enumerate(TRACES.items()):
        at = started + timedelta(seconds=offset)
        root = uuid.uuid4().hex[:16]
        for position, (service_name, kind, status, duration, attrs) in enumerate(spans):
            span_rows.append(
                [
                    tenant_id,
                    PROJECT,
                    ids[name],
                    root if position == 0 else uuid.uuid4().hex[:16],
                    "" if position == 0 else root,
                    service_name,
                    "call",
                    kind,
                    at,
                    at,
                    duration,
                    status,
                    attrs,
                ]
            )
        summary_rows.append(
            [
                tenant_id,
                PROJECT,
                at,
                ids[name],
                at,
                max(span[3] for span in spans),
                len(spans),
                sum(1 for span in spans if span[2] == "ERROR"),
                # The worker's worst status: 2 for an error, 1 for OK (`traces/queries.py`).
                2 if any(span[2] == "ERROR" for span in spans) else 1,
                spans[0][0],
                "call",
                sorted({span[0] for span in spans}),
            ]
        )
    await client.insert(
        "spans",
        span_rows,
        column_names=[
            "tenant_id", "project_id", "trace_id", "span_id", "parent_span_id",
            "service_name", "span_name", "span_kind", "timestamp", "received_at",
            "duration_ms", "status_code", "span_attrs",
        ],
    )  # fmt: skip
    await client.insert(
        "trace_summaries",
        summary_rows,
        column_names=[
            "tenant_id", "project_id", "start_time", "trace_id", "received_at", "duration_ms",
            "span_count", "error_count", "worst_severity", "root_service", "root_name",
            "span_services",
        ],
    )  # fmt: skip
    try:
        yield client, tenant_id, ids, started
    finally:
        for table in WRITTEN:
            await client.command(
                f"DELETE FROM {table} WHERE tenant_id = {{tenant_id:String}}",
                parameters={"tenant_id": tenant_id},
            )
        await client.close()


def _filters(search: dict[str, str]) -> Any:
    """The link's search as the trace route reads it (`traces/router.py`)."""
    minimum = search.get("min_duration_ms")
    return service.build_filters(
        service_name=search.get("service"),
        operation=None,
        status=search.get("status"),
        min_duration_ms=float(minimum) if minimum is not None else None,
        max_duration_ms=None,
        attribute_path=search.get("attr"),
        attribute_value=search.get("attr_value"),
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_each_callee_shape_s_link_finds_its_traces(
    seeded: tuple[AsyncClient, str, dict[str, str], datetime], case: dict[str, Any]
) -> None:
    client, tenant_id, ids, started = seeded

    page = await service.list_traces(
        TenantScopedRepository(client, tenant_id=tenant_id),
        project_id=PROJECT,
        from_time=started - timedelta(minutes=5),
        to_time=started + timedelta(minutes=5),
        filters=_filters(case["search"]),
        limit=50,
        cursor=None,
    )

    by_id = {trace_id: name for name, trace_id in ids.items()}
    found = sorted(by_id[trace.trace_id] for trace in page.traces)
    assert found == sorted(case["finds"])


def test_every_shape_the_design_names_is_in_the_fixture() -> None:
    """AC105: itself, each of the three attributes, and none."""
    attributes = {case["target"]["callee_attr"] for case in CASES}
    assert attributes == {None, "service.peer.name", "peer.service", "server.address"}
    assert any(not case["target"]["callee"].startswith("(") for case in CASES)
