"""A latency target's count, end to end through the real receiver, Kafka and worker (F19
plan T2.1, AC3's live half).

Beside `test_system.py`, but a different kind of fact: not about the deployment, about
what the worker writes. Four `SERVER` spans are sent with durations at, under and over
the boundary a 500 ms target snaps to (487.2 ms), and the merged buckets the worker
wrote must count exactly the two at or under it. So Python's boundary and the worker's
bucketing are proved to agree on real spans, not only on their constants
(`test_slo_model.py` holds those equal).

It fails when the stack is not up, as every live suite does (`live.py`).
"""

from __future__ import annotations

import base64
import os
import secrets
import socket
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import httpx
import pytest
from clickhouse_cleanup import delete_after_run
from live import require_stack
from nexovue_platform import slo
from pydantic import SecretStr

from nexovue_api.settings import API_PREFIX

API_ORIGIN: Final = os.environ.get("NEXOVUE_API_ORIGIN", "http://localhost:18000")
RECEIVER_ORIGIN: Final = os.environ.get("NEXOVUE_RECEIVER_ORIGIN", "http://localhost:14318")
CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
KAFKA_BROKERS: Final = os.environ.get("NEXOVUE_KAFKA_BROKERS", "localhost:19092")

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
# A rollup row is written once its minute and the worker's late grace have passed.
ROLLUP_DEADLINE_SECONDS: Final = 180
POLL_SECONDS: Final = 3
OTLP_KIND_SERVER: Final = 2
NANOS_PER_MILLISECOND: Final = 1_000_000
BACKDATE_MS: Final = 1_000
ASKED_MS: Final = 500.0
_WRITTEN_TABLES: Final = (
    "spans",
    "span_trace_lookup",
    "trace_summaries",
    "endpoint_stats_1m",
    "service_stats_1m",
    "service_versions_1m",
    "service_edges_1m",
)


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url if "://" in url else f"tcp://{url}")
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port),
            REACHABILITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=all(
        (
            _reachable(API_ORIGIN, 80),
            _reachable(RECEIVER_ORIGIN, 80),
            _reachable(CLICKHOUSE_DSN, 8123),
            _reachable(KAFKA_BROKERS, 9092),
        )
    ),
    suite="F19's latency system suite",
    remedy="the dev stack is not running — start it with `just dev`",
)


@dataclass(frozen=True, slots=True)
class Tenant:
    org_id: str
    project_id: str
    secret_key: SecretStr


@pytest.fixture(scope="module")
def tenant() -> Iterator[Tenant]:
    suffix = uuid.uuid4().hex[:12]
    with httpx.Client(base_url=API_ORIGIN, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = client.post(
            f"{API_PREFIX}/signup",
            json={
                "organization_name": f"SLO latency {suffix}",
                "email": f"slo-latency-{suffix}@nexovue.test",
                "password": f"slo-latency-{suffix}-password",
            },
        )
    assert response.status_code == HTTPStatus.CREATED, response.text
    body: dict[str, Any] = response.json()
    made = Tenant(
        org_id=body["organization"]["id"],
        project_id=body["project"]["id"],
        secret_key=SecretStr(body["api_key"]),
    )
    for table in _WRITTEN_TABLES:
        delete_after_run(table, made.org_id)
    yield made


@pytest.fixture(scope="module")
def clickhouse() -> Iterator[Any]:
    url = urlsplit(CLICKHOUSE_DSN)
    client = clickhouse_connect.get_client(
        host=url.hostname or "localhost",
        port=url.port or 8123,
        username=url.username or "",
        password=url.password or "",
        database=(url.path or "/").lstrip("/"),
    )
    yield client
    client.close()


def _wire(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _span(trace_id: bytes, started_ms: int, duration_ns: int) -> dict[str, Any]:
    start_ns = started_ms * NANOS_PER_MILLISECOND
    return {
        "traceId": _wire(trace_id),
        "spanId": _wire(secrets.token_bytes(8)),
        "name": "GET /checkout",
        "kind": OTLP_KIND_SERVER,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + duration_ns),
    }


def test_ac3_the_worker_counts_at_or_under_the_boundary_python_names(
    tenant: Tenant, clickhouse: Any
) -> None:
    index = slo.boundary_at_or_under(ASKED_MS)
    assert index is not None
    boundary_ns = slo.boundary_ms(index) * NANOS_PER_MILLISECOND
    durations_ns = [
        int(boundary_ns) - 5 * NANOS_PER_MILLISECOND,  # under
        int(boundary_ns),  # at: a whole nanosecond at or under the boundary
        int(boundary_ns) + NANOS_PER_MILLISECOND,  # just over
        int(ASKED_MS * NANOS_PER_MILLISECOND),  # what was typed, which is over
    ]
    service = f"latency-{uuid.uuid4().hex[:8]}"
    started = int(time.time() * 1000) - BACKDATE_MS
    trace_id = secrets.token_bytes(16)
    response = httpx.post(
        f"{RECEIVER_ORIGIN}/v1/traces",
        json={
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": service}}]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "slo-system"},
                            "spans": [_span(trace_id, started, d) for d in durations_ns],
                        }
                    ],
                }
            ]
        },
        headers={"x-nexovue-key": tenant.secret_key.get_secret_value()},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    assert response.status_code == HTTPStatus.OK, response.text

    deadline = time.monotonic() + ROLLUP_DEADLINE_SECONDS
    merged: list[int] = []
    while time.monotonic() < deadline:
        rows = clickhouse.query(
            "SELECT sum(request_count), sumForEachMerge(latency_buckets) FROM service_stats_1m "
            "WHERE tenant_id = {tenant:String} AND project_id = {project:String} "
            "AND service_name = {service:String} HAVING count() > 0",
            parameters={"tenant": tenant.org_id, "project": tenant.project_id, "service": service},
        ).result_rows
        if rows and int(rows[0][0]) == len(durations_ns):
            merged = [int(count) for count in rows[0][1]]
            break
        time.sleep(POLL_SECONDS)
    else:
        pytest.fail(f"the spans did not reach service_stats_1m within {ROLLUP_DEADLINE_SECONDS}s")

    assert slo.at_or_under(merged, index) == 2
    assert sum(merged) == len(durations_ns)
