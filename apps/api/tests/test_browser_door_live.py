"""The browser door stops counting in the rollups, end to end (docs/changes/browser-door).

A site key is printed in its page, so anyone holding the page can send to the
browser door. This suite sends what such a person could, through the real
receiver, Kafka and worker, and reads ClickHouse for what landed:

- a forged `SERVER` span reaches `spans` (the trace view) and none of
  `service_stats_1m`, `endpoint_stats_1m` or `service_versions_1m` — AC37, the
  condition F19's and F28's Step 4 wait on (F19 design *Forged spans*; F28 D55);
- a tenant over the service cap keeps its busiest service, and the minute is
  recorded in `services_over_cap_1m`;
- a forged `synthetic.check.*` reading or evidence record, through the browser
  door or a secret key, reaches neither `metrics_gauge` nor `logs`, while the
  prober's own reading still does.

Each assertion of absence waits first for a control sent beside it to land, so
"nothing there" means the worker handled it, not that it has not got there yet.

It fails when the stack is not up, as every live suite does (`live.py`).
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import socket
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import urlsplit

import asyncpg
import clickhouse_connect
import httpx
import pytest
from clickhouse_cleanup import delete_after_run
from live import require_stack
from pydantic import SecretStr

from nexovue_api.settings import API_PREFIX

API_ORIGIN: Final = os.environ.get("NEXOVUE_API_ORIGIN", "http://localhost:18000")
RECEIVER_ORIGIN: Final = os.environ.get("NEXOVUE_RECEIVER_ORIGIN", "http://localhost:14318")
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
KAFKA_BROKERS: Final = os.environ.get("NEXOVUE_KAFKA_BROKERS", "localhost:19092")

# Where the prober publishes its readings (nexovue_scheduler settings).
METRICS_TOPIC: Final = "metrics-raw"

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0

# A rollup row is written once its minute and the worker's 10-second late grace
# have passed, so a span sent now is counted within about 70 seconds. The
# ceiling leaves a slow machine room to fail on a real problem.
ROLLUP_DEADLINE_SECONDS: Final = 180
RAW_DEADLINE_SECONDS: Final = 60
POLL_SECONDS: Final = 3

OTLP_KIND_SERVER: Final = 2
OTLP_STATUS_ERROR: Final = 2
SPAN_DURATION_MS: Final = 40
NANOS_PER_MILLISECOND: Final = 1_000_000
# Far enough back that the receiver's clock and this one agree on the minute.
BACKDATE_MS: Final = 1_000

# The worker's per-tenant cap (derive.MaxServicesPerTenant).
SERVICE_CAP: Final = 2000
BUSY_REQUESTS: Final = 5

ALLOWED_ORIGIN: Final = "https://shop.browser-door.test"
SYNTHETIC_STATUS: Final = "synthetic.check.status"

# Every table a test here writes to, deleted at the end of the run.
_WRITTEN_TABLES: Final = (
    "spans",
    "span_trace_lookup",
    "trace_summaries",
    "metrics_sum",
    "endpoint_stats_1m",
    "service_stats_1m",
    "service_versions_1m",
    "services_over_cap_1m",
    "service_edges_1m",
    "metrics_gauge",
    "logs",
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
            _reachable(POSTGRES_DSN, 5432),
            _reachable(CLICKHOUSE_DSN, 8123),
            _reachable(KAFKA_BROKERS, 9092),
        )
    ),
    suite="the live browser-door suite",
    remedy="the dev stack is not running — start it with `just dev`",
)


# ------------------------------------------------------------------- the stack


@dataclass(frozen=True, slots=True)
class Tenant:
    """A signed-up organization, its secret ingest key and a public site key.

    The secret key is a `SecretStr` so a failing assertion's repr cannot print
    it; the site key is printed in a customer's page anyway (`SiteKeySummary`).
    """

    org_id: str
    project_id: str
    secret_key: SecretStr
    site_key: str


def _sign_up(client: httpx.Client) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        f"{API_PREFIX}/signup",
        json={
            "organization_name": f"Browser Door {suffix}",
            # .test is reserved by RFC 2606 and can never be a real inbox.
            "email": f"browser-door-{suffix}@nexovue.test",
            "password": f"browser-door-{suffix}-password",
        },
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


def _soft_delete(org_id: str) -> None:
    async def run() -> None:
        connection = await asyncpg.connect(POSTGRES_DSN)
        try:
            await connection.execute(
                "UPDATE organizations SET deleted_at = now() WHERE id = $1", uuid.UUID(org_id)
            )
        finally:
            await connection.close()

    asyncio.run(run())


@pytest.fixture(scope="module")
def tenant() -> Iterator[Tenant]:
    """A customer, signed up as one would, holding both kinds of key."""
    with httpx.Client(base_url=API_ORIGIN, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        signed_up = _sign_up(client)
        project_id = signed_up["project"]["id"]
        minted = client.post(
            f"{API_PREFIX}/site-keys",
            json={"project_id": project_id, "name": "shop", "allowed_origins": [ALLOWED_ORIGIN]},
        )
        assert minted.status_code == HTTPStatus.CREATED, minted.text
        created = Tenant(
            org_id=signed_up["organization"]["id"],
            project_id=project_id,
            secret_key=SecretStr(signed_up["api_key"]),
            site_key=minted.json()["public_key"],
        )
    for table in _WRITTEN_TABLES:
        delete_after_run(table, created.org_id)
    yield created
    _soft_delete(created.org_id)


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


# ------------------------------------------------------------------ the sending


def _wire(raw: bytes) -> str:
    # The receiver reads OTLP/JSON with protojson, which takes `bytes` as base64.
    return base64.b64encode(raw).decode("ascii")


def _string(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _started_ms() -> int:
    return int(time.time() * 1000) - BACKDATE_MS


def _server_spans(service: str, count: int, started_ms: int, trace_id: bytes) -> dict[str, Any]:
    """One resource of `count` errored SERVER spans, kept whole by the sampler."""
    spans = [
        {
            "traceId": _wire(trace_id),
            "spanId": _wire(secrets.token_bytes(8)),
            "name": "GET /checkout",
            "kind": OTLP_KIND_SERVER,
            "startTimeUnixNano": str(started_ms * NANOS_PER_MILLISECOND),
            "endTimeUnixNano": str((started_ms + SPAN_DURATION_MS) * NANOS_PER_MILLISECOND),
            "status": {"code": OTLP_STATUS_ERROR, "message": "browser-door suite"},
        }
        for _ in range(count)
    ]
    return {
        "resource": {
            "attributes": [_string("service.name", service), _string("service.version", "v1")]
        },
        "scopeSpans": [{"scope": {"name": "browser-door"}, "spans": spans}],
    }


def _send(path: str, key: SecretStr | str, body: dict[str, Any], *, browser: bool) -> None:
    # The only disclosure of a secret key: the header the receiver authenticates.
    headers = {"x-nexovue-key": key.get_secret_value() if isinstance(key, SecretStr) else key}
    if browser:
        # What a script that sets its own header sends; the allow-list admits it.
        headers["origin"] = ALLOWED_ORIGIN
    response = httpx.post(
        f"{RECEIVER_ORIGIN}{path}", json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
    )
    assert response.status_code == HTTPStatus.OK, f"the receiver refused {path}: {response.text}"


# ------------------------------------------------------------------ the reading


def _rows(clickhouse: Any, sql: str, **parameters: Any) -> list[tuple[Any, ...]]:
    return list(clickhouse.query(sql, parameters=parameters).result_rows)


def _wait_until(what: str, deadline_seconds: int, landed: Callable[[], bool]) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if landed():
            return
        time.sleep(POLL_SECONDS)
    pytest.fail(f"{what} did not land within {deadline_seconds}s")


def _service_requests(clickhouse: Any, table: str, tenant: Tenant, service: str) -> int:
    rows = _rows(
        clickhouse,
        f"SELECT sum(request_count) FROM {table} "
        "WHERE tenant_id = {tenant:String} AND service_name = {service:String}",
        tenant=tenant.org_id,
        service=service,
    )
    return int(rows[0][0] or 0)


def _span_rows(clickhouse: Any, tenant: Tenant, service: str) -> int:
    rows = _rows(
        clickhouse,
        "SELECT count() FROM spans "
        "WHERE tenant_id = {tenant:String} AND service_name = {service:String}",
        tenant=tenant.org_id,
        service=service,
    )
    return int(rows[0][0])


ROLLUPS: Final = ("service_stats_1m", "endpoint_stats_1m", "service_versions_1m")


# -------------------------------------------------------------------- the tests


def test_a_forged_server_span_reaches_the_trace_view_and_no_rollup(
    tenant: Tenant, clickhouse: Any
) -> None:
    """AC37: a SERVER span through the browser door counts in no rollup (T1.3)."""
    suffix = uuid.uuid4().hex[:8]
    forged, control = f"forged-{suffix}", f"control-{suffix}"
    started = _started_ms()
    _send(
        "/browser/v1/traces",
        tenant.site_key,
        {"resourceSpans": [_server_spans(forged, 3, started, secrets.token_bytes(16))]},
        browser=True,
    )
    _send(
        "/v1/traces",
        tenant.secret_key,
        {"resourceSpans": [_server_spans(control, 3, started, secrets.token_bytes(16))]},
        browser=False,
    )

    _wait_until(
        "the secret-key control in all three rollups",
        ROLLUP_DEADLINE_SECONDS,
        lambda: all(
            _service_requests(clickhouse, table, tenant, control) == 3 for table in ROLLUPS
        ),
    )
    _wait_until(
        "the forged span in `spans`",
        RAW_DEADLINE_SECONDS,
        lambda: _span_rows(clickhouse, tenant, forged) == 3,
    )

    for table in ROLLUPS:
        assert _service_requests(clickhouse, table, tenant, forged) == 0, (
            f"a browser-door SERVER span was counted in {table}: anyone holding the page "
            "can forge this service's traffic"
        )


def test_going_over_the_cap_keeps_the_busiest_and_is_recorded(
    tenant: Tenant, clickhouse: Any
) -> None:
    """2,001 minted one-request names cannot evict a busy service, and the minute
    the tenant went over is readable by tenant and minute (T2.1, T3.3)."""
    started = _started_ms()
    minute = started // 60_000 * 60
    trace_id = secrets.token_bytes(16)
    minted = [
        _server_spans(f"aaa-{index:05d}", 1, started, trace_id) for index in range(SERVICE_CAP + 1)
    ]
    busy = f"zzz-busy-{uuid.uuid4().hex[:8]}"
    _send(
        "/v1/traces",
        tenant.secret_key,
        {"resourceSpans": [*minted, _server_spans(busy, BUSY_REQUESTS, started, trace_id)]},
        browser=False,
    )

    def recorded() -> list[tuple[Any, ...]]:
        return _rows(
            clickhouse,
            "SELECT sum(services_left_out) FROM services_over_cap_1m "
            "WHERE tenant_id = {tenant:String} AND minute = toDateTime({minute:UInt32}) "
            "HAVING count() > 0",
            tenant=tenant.org_id,
            minute=minute,
        )

    _wait_until("the over-cap record", ROLLUP_DEADLINE_SECONDS, lambda: bool(recorded()))

    left_out = int(recorded()[0][0])
    assert left_out >= 2, (
        f"services_left_out = {left_out}; at least 2 of 2,002 services exceed the cap"
    )
    kept = _rows(
        clickhouse,
        "SELECT service_name, sum(request_count) FROM service_stats_1m "
        "WHERE tenant_id = {tenant:String} AND minute = toDateTime({minute:UInt32}) "
        "GROUP BY service_name",
        tenant=tenant.org_id,
        minute=minute,
    )
    assert len(kept) == SERVICE_CAP, f"kept {len(kept)} services in the minute, want the cap"
    assert {str(name): int(count) for name, count in kept}.get(busy) == BUSY_REQUESTS, (
        "the busiest service was evicted by minted names that sort first"
    )


def _gauge(name: str, service: str, check_id: str | None) -> dict[str, Any]:
    attributes = [_string("service.name", service)]
    if check_id is not None:
        attributes.append(_string("synthetic.check.id", check_id))
    now_ns = str(time.time_ns())
    return {
        "resource": {"attributes": attributes},
        "scopeMetrics": [
            {
                "scope": {"name": "browser-door"},
                "metrics": [
                    {
                        "name": name,
                        "unit": "1",
                        "gauge": {"dataPoints": [{"timeUnixNano": now_ns, "asInt": "1"}]},
                    }
                ],
            }
        ],
    }


def _log(service: str, check_id: str | None) -> dict[str, Any]:
    attributes = [_string("service.name", service)]
    if check_id is not None:
        attributes.append(_string("synthetic.check.id", check_id))
    return {
        "resource": {"attributes": attributes},
        "scopeLogs": [
            {
                "scope": {"name": "browser-door"},
                "logRecords": [
                    {
                        "timeUnixNano": str(time.time_ns()),
                        "severityText": "INFO",
                        "body": {"stringValue": '{"outcome": "up"}'},
                    }
                ],
            }
        ],
    }


def _publish_prober_reading(tenant: Tenant, check_id: str) -> None:
    """A reading the way the prober publishes one: its own envelope, its own door."""
    from confluent_kafka import Producer

    from nexovue_scheduler.probes.result import RunMeasurement
    from nexovue_scheduler.publish import build_envelope

    envelope = build_envelope(
        RunMeasurement(
            check_id=check_id,
            check_type="api",
            run_id=uuid.uuid4().hex,
            outcome="up",
            attempt=1,
            final=True,
            maintenance=False,
            location="local",
            total_seconds=0.2,
        ),
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        observed_at_unix_nano=time.time_ns(),
        environment="development",
        region="local",
    )
    producer = Producer({"bootstrap.servers": KAFKA_BROKERS, "acks": "all"})
    producer.produce(METRICS_TOPIC, key=tenant.org_id, value=envelope)
    assert producer.flush(10) == 0, "the broker did not take the prober's reading"


def _check_rows(clickhouse: Any, table: str, tenant: Tenant, check_id: str) -> int:
    rows = _rows(
        clickhouse,
        f"SELECT count() FROM {table} "
        "WHERE tenant_id = {tenant:String} "
        "AND toString(resource_attrs.synthetic.check.id) = {check:String}",
        tenant=tenant.org_id,
        check=check_id,
    )
    return int(rows[0][0])


def _service_rows(clickhouse: Any, table: str, tenant: Tenant, service: str) -> int:
    rows = _rows(
        clickhouse,
        f"SELECT count() FROM {table} "
        "WHERE tenant_id = {tenant:String} AND service_name = {service:String}",
        tenant=tenant.org_id,
        service=service,
    )
    return int(rows[0][0])


def test_a_forged_check_reading_or_evidence_is_dropped_and_the_probers_kept(
    tenant: Tenant, clickhouse: Any
) -> None:
    """`synthetic.*` readings and evidence come only from the prober's door, through
    the browser door or a secret key alike (T4.1, T4.2, decision 5)."""
    suffix = uuid.uuid4().hex[:8]
    forged_check, prober_check = f"forged-check-{suffix}", f"prober-check-{suffix}"
    control = f"control-{suffix}"
    doors: tuple[tuple[str, SecretStr | str, bool], ...] = (
        ("/browser/v1", tenant.site_key, True),
        ("/v1", tenant.secret_key, False),
    )
    for prefix, key, browser in doors:
        _send(
            f"{prefix}/metrics",
            key,
            {
                "resourceMetrics": [
                    _gauge(SYNTHETIC_STATUS, "nexovue-prober", forged_check),
                    _gauge("browser.door.control", control, None),
                ]
            },
            browser=browser,
        )
        _send(
            f"{prefix}/logs",
            key,
            {"resourceLogs": [_log("nexovue-prober", forged_check), _log(control, None)]},
            browser=browser,
        )
    _publish_prober_reading(tenant, prober_check)

    _wait_until(
        "both doors' controls and the prober's reading",
        RAW_DEADLINE_SECONDS,
        lambda: (
            _service_rows(clickhouse, "metrics_gauge", tenant, control) == 2
            and _service_rows(clickhouse, "logs", tenant, control) == 2
            and _check_rows(clickhouse, "metrics_gauge", tenant, prober_check) > 0
        ),
    )

    assert _check_rows(clickhouse, "metrics_gauge", tenant, forged_check) == 0, (
        "a forged synthetic reading reached metrics_gauge: a down check could read up"
    )
    assert _check_rows(clickhouse, "logs", tenant, forged_check) == 0, (
        "forged synthetic evidence reached logs"
    )
