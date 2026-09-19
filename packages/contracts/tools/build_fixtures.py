"""Regenerate the golden fixtures in ../fixtures.

One committed envelope per shape a door sends — door 3 sends two signals from one
page view, so it has two. Every value here is fixed — no clocks, no randomness —
so a rebuild that changes a byte means the contract or the payload shape changed,
and the diff says which.

Run: uv run --no-project --with protobuf --with opentelemetry-proto \
         python packages/contracts/tools/build_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONTRACTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CONTRACTS_ROOT / "gen" / "python"))

from nexovue.v1 import envelope_pb2, profiles_pb2  # noqa: E402
from opentelemetry.proto.collector.logs.v1 import (  # noqa: E402
    logs_service_pb2,
)
from opentelemetry.proto.collector.metrics.v1 import (  # noqa: E402
    metrics_service_pb2,
)
from opentelemetry.proto.collector.trace.v1 import (  # noqa: E402
    trace_service_pb2,
)
from opentelemetry.proto.common.v1 import common_pb2  # noqa: E402
from opentelemetry.proto.logs.v1 import logs_pb2  # noqa: E402
from opentelemetry.proto.metrics.v1 import metrics_pb2  # noqa: E402
from opentelemetry.proto.resource.v1 import resource_pb2  # noqa: E402
from opentelemetry.proto.trace.v1 import trace_pb2  # noqa: E402

FIXTURES_DIR = CONTRACTS_ROOT / "fixtures"

# Ids are UUIDv7-shaped and fixed, so fixtures diff cleanly across rebuilds.
TENANT_ID = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"

# The OTel semantic-convention generation these payloads speak.
SEMCONV_VERSION = "1.44.0"

NANOS_PER_SECOND = 1_000_000_000
NANOS_PER_MILLISECOND = 1_000_000

# 2026-08-26T09:15:00Z, the notional wall clock for every fixture.
BASE_UNIX_NANO = 1787649300 * NANOS_PER_SECOND


def string_attribute(key: str, value: str) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(string_value=value))


def int_attribute(key: str, value: int) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(int_value=value))


def double_attribute(key: str, value: float) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(double_value=value))


def bool_attribute(key: str, value: bool) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(bool_value=value))


def string_array_attribute(key: str, values: list[str]) -> common_pb2.KeyValue:
    """An attribute the registry types string[] — browser.brands is the one here."""
    return common_pb2.KeyValue(
        key=key,
        value=common_pb2.AnyValue(
            array_value=common_pb2.ArrayValue(
                values=[common_pb2.AnyValue(string_value=value) for value in values]
            )
        ),
    )


def resource_for(
    service_name: str,
    service_version: str,
    sdk_language: str,
    *,
    extra: list[common_pb2.KeyValue] | None = None,
) -> resource_pb2.Resource:
    """A resource carrying what every real SDK puts there, plus door-specific extras."""
    attributes = [
        string_attribute("service.name", service_name),
        string_attribute("service.version", service_version),
        string_attribute("deployment.environment.name", "production"),
        string_attribute("telemetry.sdk.name", "opentelemetry"),
        string_attribute("telemetry.sdk.language", sdk_language),
        string_attribute("telemetry.sdk.version", "1.46.0"),
    ]
    attributes.extend(extra or [])
    return resource_pb2.Resource(attributes=attributes)


def envelope(
    door: envelope_pb2.Door,
    signal: envelope_pb2.Signal,
    ingest_id: str,
    received_offset_millis: int,
    payload: bytes,
) -> envelope_pb2.Envelope:
    return envelope_pb2.Envelope(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        door=door,
        signal=signal,
        received_at_unix_nano=BASE_UNIX_NANO + received_offset_millis * NANOS_PER_MILLISECOND,
        ingest_id=ingest_id,
        semconv_version=SEMCONV_VERSION,
        payload=payload,
    )


# ---------------------------------------------------------------- door 1: OTLP


def door1_traces() -> envelope_pb2.Envelope:
    """A customer SDK's checkout request: one trace, server span with a failing DB child.

    Already split to a single trace_id, which is what the receiver publishes.
    """
    trace_id = bytes.fromhex("4bf92f3577b34da6a3ce929d0e0e4736")
    server_span_id = bytes.fromhex("00f067aa0ba902b7")
    db_span_id = bytes.fromhex("00f067aa0ba902c8")
    start = BASE_UNIX_NANO

    server_span = trace_pb2.Span(
        trace_id=trace_id,
        span_id=server_span_id,
        name="POST /api/checkout",
        kind=trace_pb2.Span.SPAN_KIND_SERVER,
        start_time_unix_nano=start,
        end_time_unix_nano=start + 412 * NANOS_PER_MILLISECOND,
        attributes=[
            string_attribute("http.request.method", "POST"),
            string_attribute("url.path", "/api/checkout"),
            string_attribute("url.scheme", "https"),
            int_attribute("http.response.status_code", 500),
            string_attribute("server.address", "shop.acme.example"),
            int_attribute("server.port", 443),
            string_attribute("network.protocol.version", "1.1"),
            string_attribute("client.address", "203.0.113.42"),
            string_attribute(
                "user_agent.original", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            ),
            string_attribute("error.type", "500"),
        ],
        status=trace_pb2.Status(
            code=trace_pb2.Status.STATUS_CODE_ERROR,
            message="payment authorization failed",
        ),
    )

    db_span = trace_pb2.Span(
        trace_id=trace_id,
        span_id=db_span_id,
        parent_span_id=server_span_id,
        name="INSERT shop.orders",
        kind=trace_pb2.Span.SPAN_KIND_CLIENT,
        start_time_unix_nano=start + 31 * NANOS_PER_MILLISECOND,
        end_time_unix_nano=start + 398 * NANOS_PER_MILLISECOND,
        attributes=[
            string_attribute("db.system.name", "postgresql"),
            string_attribute("db.namespace", "shop"),
            string_attribute("db.collection.name", "orders"),
            string_attribute("db.operation.name", "INSERT"),
            string_attribute(
                "db.query.text",
                "INSERT INTO orders (id, customer_id, total_cents) VALUES ($1, $2, $3)",
            ),
            string_attribute("server.address", "orders-db.internal"),
            int_attribute("server.port", 5432),
            string_attribute("error.type", "psycopg.errors.LockNotAvailable"),
        ],
        status=trace_pb2.Status(
            code=trace_pb2.Status.STATUS_CODE_ERROR,
            message="canceling statement due to lock timeout",
        ),
        events=[
            trace_pb2.Span.Event(
                time_unix_nano=start + 397 * NANOS_PER_MILLISECOND,
                name="exception",
                attributes=[
                    string_attribute("exception.type", "psycopg.errors.LockNotAvailable"),
                    string_attribute(
                        "exception.message", "canceling statement due to lock timeout"
                    ),
                    string_attribute(
                        "exception.stacktrace",
                        'File "/app/orders/repository.py", line 88, in create_order\n'
                        "    await cursor.execute(INSERT_ORDER, params)\n",
                    ),
                ],
            )
        ],
    )

    request = trace_service_pb2.ExportTraceServiceRequest(
        resource_spans=[
            trace_pb2.ResourceSpans(
                resource=resource_for("checkout-api", "2.14.3", "python"),
                scope_spans=[
                    trace_pb2.ScopeSpans(
                        scope=common_pb2.InstrumentationScope(
                            name="opentelemetry.instrumentation.fastapi",
                            version="0.55b0",
                        ),
                        spans=[server_span, db_span],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_SPANS,
        "01996d5e-9a40-7c01-a000-000000000001",
        received_offset_millis=430,
        payload=request.SerializeToString(),
    )


def door1_logs() -> envelope_pb2.Envelope:
    """Two application log records, one of them correlated to the trace above."""
    trace_id = bytes.fromhex("4bf92f3577b34da6a3ce929d0e0e4736")
    span_id = bytes.fromhex("00f067aa0ba902c8")
    start = BASE_UNIX_NANO

    correlated_error = logs_pb2.LogRecord(
        time_unix_nano=start + 398 * NANOS_PER_MILLISECOND,
        observed_time_unix_nano=start + 399 * NANOS_PER_MILLISECOND,
        severity_number=logs_pb2.SEVERITY_NUMBER_ERROR,
        severity_text="ERROR",
        body=common_pb2.AnyValue(
            string_value="order 8814 failed: canceling statement due to lock timeout"
        ),
        attributes=[
            string_attribute("code.function.name", "create_order"),
            string_attribute("code.file.path", "/app/orders/repository.py"),
            int_attribute("code.line.number", 88),
            string_attribute("exception.type", "psycopg.errors.LockNotAvailable"),
            int_attribute("order.id", 8814),
        ],
        trace_id=trace_id,
        span_id=span_id,
        flags=1,
    )

    routine_info = logs_pb2.LogRecord(
        time_unix_nano=start + 1_200 * NANOS_PER_MILLISECOND,
        observed_time_unix_nano=start + 1_201 * NANOS_PER_MILLISECOND,
        severity_number=logs_pb2.SEVERITY_NUMBER_INFO,
        severity_text="INFO",
        body=common_pb2.AnyValue(string_value="order queue drained, depth=0"),
        attributes=[string_attribute("messaging.destination.name", "orders.retry")],
    )

    request = logs_service_pb2.ExportLogsServiceRequest(
        resource_logs=[
            logs_pb2.ResourceLogs(
                resource=resource_for("checkout-api", "2.14.3", "python"),
                scope_logs=[
                    logs_pb2.ScopeLogs(
                        scope=common_pb2.InstrumentationScope(
                            name="opentelemetry.instrumentation.logging", version="0.55b0"
                        ),
                        log_records=[correlated_error, routine_info],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_LOGS,
        "01996d5e-9a40-7c01-a000-000000000002",
        received_offset_millis=1_260,
        payload=request.SerializeToString(),
    )


def door1_metrics() -> envelope_pb2.Envelope:
    """Host metrics plus a request-duration histogram — the two shapes that behave differently."""
    start = BASE_UNIX_NANO
    end = start + 60 * NANOS_PER_SECOND

    memory_usage = metrics_pb2.Metric(
        name="system.memory.usage",
        description="Reported memory in use",
        unit="By",
        sum=metrics_pb2.Sum(
            aggregation_temporality=metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE,
            is_monotonic=False,
            data_points=[
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_int=6_442_450_944,
                    attributes=[string_attribute("system.memory.state", "used")],
                ),
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_int=2_147_483_648,
                    attributes=[string_attribute("system.memory.state", "free")],
                ),
            ],
        ),
    )

    request_duration = metrics_pb2.Metric(
        name="http.server.request.duration",
        description="Duration of HTTP server requests",
        unit="s",
        histogram=metrics_pb2.Histogram(
            aggregation_temporality=metrics_pb2.AGGREGATION_TEMPORALITY_DELTA,
            data_points=[
                metrics_pb2.HistogramDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    count=1_284,
                    sum=61.632,
                    min=0.004,
                    max=1.918,
                    # OTel's default explicit bucket boundaries for request duration.
                    explicit_bounds=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0],
                    bucket_counts=[12, 190, 420, 380, 150, 60, 40, 20, 8, 3, 1],
                    attributes=[
                        string_attribute("http.request.method", "POST"),
                        string_attribute("http.route", "/api/checkout"),
                        int_attribute("http.response.status_code", 200),
                    ],
                )
            ],
        ),
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=resource_for(
                    "checkout-api",
                    "2.14.3",
                    "python",
                    extra=[string_attribute("host.name", "ip-10-0-3-17")],
                ),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(
                            name="opentelemetry.instrumentation.system_metrics",
                            version="0.55b0",
                        ),
                        metrics=[memory_usage, request_duration],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000003",
        received_offset_millis=60_080,
        payload=request.SerializeToString(),
    )


def door1_profiles() -> envelope_pb2.Envelope:
    """CPU samples from the eBPF profiler, as the receiver converts them (ADR-015).

    Three samples of one service: a named Go stack linked to a span, the same stack
    again, and one whose leaf the profiler could not name — the three shapes the
    worker stores differently.
    """
    handler = [
        profiles_pb2.Frame(function="crypto/sha256.block", file="sha256block.go", line=41),
        profiles_pb2.Frame(function="main.(*Cart).Checkout", file="cart.go", line=112),
        profiles_pb2.Frame(function="net/http.(*conn).serve", file="server.go", line=2092),
    ]
    unnamed = [
        profiles_pb2.Frame(binary="libc.so.6", address=0x9A4F0),
        profiles_pb2.Frame(function="main.(*Cart).Checkout", file="cart.go", line=118),
    ]
    first = BASE_UNIX_NANO + 1_000 * NANOS_PER_MILLISECOND
    payload = profiles_pb2.ProfileSamples(
        batches=[
            profiles_pb2.ProfileBatch(
                resource_attributes=resource_for("checkout", "1.4.2", "go").attributes,
                sample_type="cpu",
                sample_unit="nanoseconds",
                period=10_000_000,
                period_type="cpu",
                period_unit="nanoseconds",
                samples=[
                    profiles_pb2.Sample(
                        frames=handler,
                        value=30_000_000,
                        time_unix_nano=first,
                        trace_id=bytes.fromhex("4bf92f3577b34da6a3ce929d0e0e4736"),
                        span_id=bytes.fromhex("00f067aa0ba902b7"),
                        attributes=[string_attribute("thread.name", "worker-3")],
                    ),
                    profiles_pb2.Sample(frames=handler, value=10_000_000, time_unix_nano=first),
                    profiles_pb2.Sample(frames=unnamed, value=10_000_000, time_unix_nano=first),
                ],
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_PROFILES,
        "01996d5e-9a40-7c01-a000-00000000000a",
        received_offset_millis=1_540,
        payload=payload.SerializeToString(),
    )


# ----------------------------------------------------------- door 2: synthetic


def synthetic_resource(check_id: str, check_type: str, region: str) -> resource_pb2.Resource:
    """Our own prober identifies itself as the service; the check identity lives in attributes."""
    return resource_for(
        "nexovue-prober",
        "0.1.0",
        "python",
        extra=[
            string_attribute("synthetic.check.id", check_id),
            string_attribute("synthetic.check.type", check_type),
            string_attribute("cloud.region", region),
        ],
    )


def synthetic_gauge(
    name: str,
    unit: str,
    description: str,
    points: list[metrics_pb2.NumberDataPoint],
) -> metrics_pb2.Metric:
    return metrics_pb2.Metric(
        name=name,
        unit=unit,
        description=description,
        gauge=metrics_pb2.Gauge(data_points=points),
    )


SYNTHETIC_CHECK_ID = "01996d5e-9a40-7d01-b000-000000000101"
SYNTHETIC_RUN_ID = "3f9a1c52-7e0b-4d8a-9f61-2c4e8b7a1d05"


def synthetic_run_attributes(outcome: str) -> list[common_pb2.KeyValue]:
    """What every point and evidence record of one run carries, so a reader counts
    only confirmed, measured runs and still sees the retries (F18 design, *Results*)."""
    return [
        string_attribute("synthetic.check.outcome", outcome),
        string_attribute("synthetic.check.run.id", SYNTHETIC_RUN_ID),
        int_attribute("synthetic.check.attempt", 1),
        bool_attribute("synthetic.check.final", True),
        bool_attribute("synthetic.check.maintenance", False),
        string_attribute("synthetic.check.location", "mumbai"),
    ]


def door2_synthetic_api() -> envelope_pb2.Envelope:
    """An API check: pass/fail, total duration, per-phase breakdown, certificate expiry."""
    observed_at = BASE_UNIX_NANO
    check_id = SYNTHETIC_CHECK_ID
    run = synthetic_run_attributes("up")

    def phase(step: str, seconds: float) -> metrics_pb2.NumberDataPoint:
        return metrics_pb2.NumberDataPoint(
            time_unix_nano=observed_at,
            as_double=seconds,
            attributes=[*run, string_attribute("synthetic.check.step", step)],
        )

    status = synthetic_gauge(
        "synthetic.check.status",
        "1",
        "1 when the check passed, 0 when it failed",
        [
            metrics_pb2.NumberDataPoint(
                time_unix_nano=observed_at,
                as_int=1,
                attributes=[*run, int_attribute("http.response.status_code", 200)],
            )
        ],
    )
    duration = synthetic_gauge(
        "synthetic.check.duration",
        "s",
        "Wall-clock duration of the whole check",
        [metrics_pb2.NumberDataPoint(time_unix_nano=observed_at, as_double=0.2841, attributes=run)],
    )
    phases = synthetic_gauge(
        "synthetic.check.phase.duration",
        "s",
        "Latency breakdown of the request",
        [
            phase("dns", 0.0121),
            phase("tcp", 0.0334),
            phase("tls", 0.0908),
            phase("ttfb", 0.1302),
            phase("transfer", 0.0176),
        ],
    )
    certificate_validity = synthetic_gauge(
        "synthetic.tls.certificate.remaining",
        "d",
        "Days until the served certificate expires",
        [
            metrics_pb2.NumberDataPoint(
                time_unix_nano=observed_at,
                as_int=47,
                attributes=[
                    *run,
                    string_attribute("tls.server.issuer", "CN=E6,O=Let's Encrypt,C=US"),
                    string_attribute("server.address", "shop.acme.example"),
                ],
            )
        ],
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=synthetic_resource(check_id, "api", "mumbai"),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.checks.api", version="0.1.0"
                        ),
                        metrics=[status, duration, phases, certificate_validity],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_SYNTHETIC,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000004",
        received_offset_millis=310,
        payload=request.SerializeToString(),
    )


def door2_synthetic_evidence() -> envelope_pb2.Envelope:
    """What one API check run sent and received, as the check page shows it (F18 FR31).

    One log record per attempt, beside the run's metrics. The body is the run's
    evidence as JSON — redacted and size-capped by the prober before it leaves —
    and the attributes are the ones a reader finds it by.
    """
    observed_at = BASE_UNIX_NANO
    body = json.dumps(
        {
            "run_id": SYNTHETIC_RUN_ID,
            "check_id": SYNTHETIC_CHECK_ID,
            "check_type": "api",
            "location": "mumbai",
            "attempt": 1,
            "final": True,
            "maintenance": False,
            "outcome": "down",
            "started_at": "2026-08-26T09:15:00Z",
            "duration_ms": 284.1,
            "status_code": 200,
            "tls_days_remaining": 47,
            "steps": [
                {
                    "position": 0,
                    "name": "Create order",
                    "target": "https://api.acme.example/orders",
                    "duration_ms": 284.1,
                    "phases_ms": {
                        "dns": 12.1,
                        "tcp": 33.4,
                        "tls": 90.8,
                        "ttfb": 130.2,
                        "transfer": 17.6,
                    },
                    "request": {
                        "method": "POST",
                        "url": "https://api.acme.example/orders",
                        "headers": [
                            {"name": "Authorization", "value": "[withheld]"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                        "body": {
                            "text": '{"item": "shoe", "qty": 2}',
                            "size_bytes": 27,
                            "truncated": False,
                            "binary": False,
                        },
                    },
                    "response": {
                        "status_code": 200,
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "body": {
                            "text": '{"order": {"id": null}}',
                            "size_bytes": 24,
                            "truncated": False,
                            "binary": False,
                        },
                    },
                    "answers": [],
                    "assertions": [
                        {
                            "source": "status_code",
                            "property": "",
                            "comparison": "equals",
                            "target": "200",
                            "actual": "200",
                            "passed": True,
                        },
                        {
                            "source": "json_path",
                            "property": "$.order.id",
                            "comparison": "is_not_empty",
                            "target": "",
                            "actual": "null",
                            "passed": False,
                        },
                    ],
                    "extracted": [],
                    "passed": False,
                    "error": "expected json path $.order.id is not empty, got null",
                }
            ],
            "error": "expected json path $.order.id is not empty, got null",
        },
        separators=(",", ":"),
    )
    record = logs_pb2.LogRecord(
        time_unix_nano=observed_at,
        observed_time_unix_nano=observed_at,
        severity_number=logs_pb2.SEVERITY_NUMBER_ERROR,
        severity_text="ERROR",
        body=common_pb2.AnyValue(string_value=body),
        attributes=synthetic_run_attributes("down"),
    )
    request = logs_service_pb2.ExportLogsServiceRequest(
        resource_logs=[
            logs_pb2.ResourceLogs(
                resource=synthetic_resource(SYNTHETIC_CHECK_ID, "api", "mumbai"),
                scope_logs=[
                    logs_pb2.ScopeLogs(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.checks.api", version="0.1.0"
                        ),
                        log_records=[record],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_SYNTHETIC,
        envelope_pb2.SIGNAL_LOGS,
        "01996d5e-9a40-7c01-a000-000000000005",
        received_offset_millis=320,
        payload=request.SerializeToString(),
    )


# ----------------------------------------------------------------- door 3: RUM
#
# Door 3's client is our own browser SDK (packages/browser-sdk). It names itself
# rather than "opentelemetry" and declares no deployment environment, so
# resource_for() above does not describe it. It sends two signals from one page
# view — vitals and errors as logs, the page view as a span — and an envelope
# carries one signal, so door 3 has two fixtures.

BROWSER_SDK_NAME = "@nexovue/browser-sdk"
BROWSER_SDK_VERSION = "0.1.0"

# Both fixtures are the same visitor's single page view: session.id is the only
# join between a vital, an error and a page view, and there is no session table.
RUM_SESSION_ID = "5f3c9a17d84b42e0bb1c6d5a90e2f478"
RUM_PAGE_URL = "https://shop.acme.example/checkout"
RUM_PAGE_PATH = "/checkout"


def browser_resource() -> resource_pb2.Resource:
    """The resource the browser SDK puts on every export, spans and logs alike.

    A Chromium visitor, because the three User-Agent Client Hints attributes
    exist nowhere else: Firefox and Safari expose none of them and the SDK leaves
    all three unset rather than sending "". browser.brands is a string ARRAY.
    """
    return resource_pb2.Resource(
        attributes=[
            string_attribute("service.name", "acme-storefront"),
            string_attribute("service.version", "2026.8.14"),
            string_attribute("telemetry.sdk.name", BROWSER_SDK_NAME),
            string_attribute("telemetry.sdk.language", "webjs"),
            string_attribute("telemetry.sdk.version", BROWSER_SDK_VERSION),
            string_attribute(
                "user_agent.original",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            ),
            string_attribute("browser.language", "en-GB"),
            string_array_attribute(
                "browser.brands",
                ["Not_A Brand 24", "Chromium 142", "Google Chrome 142"],
            ),
            string_attribute("browser.platform", "macOS"),
            bool_attribute("browser.mobile", False),
        ]
    )


def rum_page_context() -> list[common_pb2.KeyValue]:
    """What every record the browser SDK sends says about where it happened."""
    return [
        string_attribute("session.id", RUM_SESSION_ID),
        string_attribute("url.full", RUM_PAGE_URL),
        string_attribute("url.path", RUM_PAGE_PATH),
    ]


def web_vital_record(
    name: str,
    value: float,
    metric_id: str,
    rating: str,
    reported_offset_millis: int,
) -> logs_pb2.LogRecord:
    """One web-vitals callback in the conventions' browser.web_vital event shape.

    The vital's name is a lowercase VALUE — there is no key per vital — and the
    value is passed through in the library's own units: milliseconds for lcp, a
    unitless ratio for cls. delta equals value on a single report.
    """
    reported_at = BASE_UNIX_NANO + reported_offset_millis * NANOS_PER_MILLISECOND
    return logs_pb2.LogRecord(
        time_unix_nano=reported_at,
        observed_time_unix_nano=reported_at,
        severity_number=logs_pb2.SEVERITY_NUMBER_INFO,
        severity_text="INFO",
        event_name="browser.web_vital",
        attributes=[
            string_attribute("browser.web_vital.name", name),
            double_attribute("browser.web_vital.value", value),
            double_attribute("browser.web_vital.delta", value),
            string_attribute("browser.web_vital.id", metric_id),
            string_attribute("browser.web_vital.rating", rating),
            string_attribute("browser.web_vital.navigation_type", "navigate"),
            *rum_page_context(),
        ],
    )


def door3_rum_logs() -> envelope_pb2.Envelope:
    """The page-hide beacon: two web vitals and an uncaught error, as log records.

    Vitals and errors travel as logs rather than as span attributes because
    nothing samples logs, while the page-view span below faces the tail sampler.
    """
    message = "n.checkout is not a function"
    thrown_at = BASE_UNIX_NANO + 6_301 * NANOS_PER_MILLISECOND

    uncaught_error = logs_pb2.LogRecord(
        time_unix_nano=thrown_at,
        observed_time_unix_nano=thrown_at,
        severity_number=logs_pb2.SEVERITY_NUMBER_ERROR,
        severity_text="ERROR",
        event_name="exception",
        body=common_pb2.AnyValue(string_value=message),
        attributes=[
            # exception.type is mandatory, not conditional: the Errors screen
            # filters exception_type != '', so a record without one is invisible.
            string_attribute("exception.type", "TypeError"),
            string_attribute("exception.message", message),
            # Raw and minified. Symbolication happens at query time.
            string_attribute(
                "exception.stacktrace",
                "TypeError: n.checkout is not a function\n"
                "    at t (https://cdn.acme.example/app.9f3c1a.min.js:2:48211)\n"
                "    at HTMLButtonElement.<anonymous> "
                "(https://cdn.acme.example/app.9f3c1a.min.js:2:12904)",
            ),
            *rum_page_context(),
        ],
        # No trace context, deliberately: error groups count distinct
        # (trace id, span id) pairs, so a page's errors sharing the page-view
        # span's context would be reported as one occurrence.
    )

    request = logs_service_pb2.ExportLogsServiceRequest(
        resource_logs=[
            logs_pb2.ResourceLogs(
                resource=browser_resource(),
                scope_logs=[
                    logs_pb2.ScopeLogs(
                        scope=common_pb2.InstrumentationScope(
                            name=BROWSER_SDK_NAME, version=BROWSER_SDK_VERSION
                        ),
                        # Arrival order: lcp finalises at the first interaction,
                        # cls only when the page is hidden.
                        log_records=[
                            web_vital_record(
                                "lcp",
                                3210.0,
                                "v6-1787649300178-4901823746152",
                                "needs-improvement",
                                4_142,
                            ),
                            uncaught_error,
                            web_vital_record(
                                "cls",
                                0.03,
                                "v6-1787649300182-7318204965731",
                                "good",
                                8_704,
                            ),
                        ],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                # No resource schema_url: the SDK declares the generation on the
                # scope only, which is where the receiver reads it from first.
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_RUM,
        envelope_pb2.SIGNAL_LOGS,
        "01996d5e-9a40-7c01-a000-000000000009",
        received_offset_millis=8_712,
        payload=request.SerializeToString(),
    )


def door3_rum_traces() -> envelope_pb2.Envelope:
    """The page view: one root span per navigation, under a fixed literal name.

    No fetch child. Backend correlation needs the customer's own API to allow the
    traceparent header, which is their configuration, and the SDK opens no span
    for a request. Received after the logs above: both leave in the page-hide
    flush, which sends logs first.
    """
    trace_id = bytes.fromhex("9c1f8e2d4a6b7c8d9e0f1a2b3c4d5e6f")
    page_view_span_id = bytes.fromhex("1a2b3c4d5e6f7a8b")
    start = BASE_UNIX_NANO

    page_view = trace_pb2.Span(
        trace_id=trace_id,
        span_id=page_view_span_id,
        # A fixed literal, never the URL: endpoint_stats_1m groups by span_name
        # and retains 13 months, so a per-URL name mints a rollup row per URL.
        name="page_view",
        kind=trace_pb2.Span.SPAN_KIND_INTERNAL,
        start_time_unix_nano=start,
        end_time_unix_nano=start + 1_940 * NANOS_PER_MILLISECOND,
        # url.query and http.route are absent: the first is opt-in because
        # masking matches attribute keys and not values, and a route template is
        # the customer's knowledge, never derived from a path.
        attributes=[*rum_page_context(), string_attribute("url.scheme", "https")],
        status=trace_pb2.Status(code=trace_pb2.Status.STATUS_CODE_UNSET),
    )

    request = trace_service_pb2.ExportTraceServiceRequest(
        resource_spans=[
            trace_pb2.ResourceSpans(
                resource=browser_resource(),
                scope_spans=[
                    trace_pb2.ScopeSpans(
                        scope=common_pb2.InstrumentationScope(
                            name=BROWSER_SDK_NAME, version=BROWSER_SDK_VERSION
                        ),
                        spans=[page_view],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_RUM,
        envelope_pb2.SIGNAL_SPANS,
        "01996d5e-9a40-7c01-a000-000000000006",
        received_offset_millis=8_719,
        payload=request.SerializeToString(),
    )


# -------------------------------------------------------------- door 4b: pollers


def door4b_cloudwatch() -> envelope_pb2.Envelope:
    """CloudWatch RDS metrics, mapped near 1:1 onto OTLP gauges by the poller."""
    start = BASE_UNIX_NANO
    end = start + 60 * NANOS_PER_SECOND
    instance = [string_attribute("aws.rds.instance.identifier", "acme-orders-prod")]

    def cloudwatch_gauge(
        name: str, unit: str, point: metrics_pb2.NumberDataPoint
    ) -> metrics_pb2.Metric:
        return metrics_pb2.Metric(
            name=name, unit=unit, gauge=metrics_pb2.Gauge(data_points=[point])
        )

    cpu = cloudwatch_gauge(
        "aws.rds.cpu.utilization",
        "%",
        metrics_pb2.NumberDataPoint(
            start_time_unix_nano=start, time_unix_nano=end, as_double=71.4, attributes=instance
        ),
    )
    connections = cloudwatch_gauge(
        "aws.rds.database.connections",
        "{connection}",
        metrics_pb2.NumberDataPoint(
            start_time_unix_nano=start, time_unix_nano=end, as_int=184, attributes=instance
        ),
    )
    free_storage = cloudwatch_gauge(
        "aws.rds.storage.free",
        "By",
        metrics_pb2.NumberDataPoint(
            start_time_unix_nano=start,
            time_unix_nano=end,
            as_int=41_875_931_136,
            attributes=instance,
        ),
    )
    read_latency = cloudwatch_gauge(
        "aws.rds.read.latency",
        "s",
        metrics_pb2.NumberDataPoint(
            start_time_unix_nano=start, time_unix_nano=end, as_double=0.0043, attributes=instance
        ),
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=resource_for(
                    "nexovue-poller",
                    "0.1.0",
                    "python",
                    extra=[
                        string_attribute("cloud.provider", "aws"),
                        string_attribute("cloud.region", "eu-west-1"),
                        string_attribute("cloud.account.id", "123456789012"),
                    ],
                ),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.pollers.cloudwatch", version="0.1.0"
                        ),
                        metrics=[cpu, connections, free_storage, read_latency],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_POLLER,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000007",
        received_offset_millis=60_140,
        payload=request.SerializeToString(),
    )


def door4b_dbstats() -> envelope_pb2.Envelope:
    """pg_stat_statements: per-statement timing keyed by query hash, never by raw text."""
    start = BASE_UNIX_NANO
    end = start + 60 * NANOS_PER_SECOND

    def statement(query_hash: str, mean_seconds: float, calls: int) -> list[common_pb2.KeyValue]:
        return [
            string_attribute("db.query.hash", query_hash),
            string_attribute("db.system.name", "postgresql"),
            string_attribute("db.namespace", "shop"),
            int_attribute("db.client.operation.calls", calls),
            double_attribute("db.client.operation.mean_time", mean_seconds),
        ]

    mean_time = metrics_pb2.Metric(
        name="db.client.operation.duration",
        unit="s",
        description="Mean execution time per normalized statement",
        gauge=metrics_pb2.Gauge(
            data_points=[
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_double=0.3184,
                    attributes=statement("-4213885652027451965", 0.3184, 1_284),
                ),
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_double=0.0021,
                    attributes=statement("8871209934417210012", 0.0021, 91_402),
                ),
            ]
        ),
    )

    connection_count = metrics_pb2.Metric(
        name="db.client.connection.count",
        unit="{connection}",
        description="Backend connections by state",
        gauge=metrics_pb2.Gauge(
            data_points=[
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_int=41,
                    attributes=[string_attribute("db.client.connection.state", "active")],
                ),
                metrics_pb2.NumberDataPoint(
                    start_time_unix_nano=start,
                    time_unix_nano=end,
                    as_int=143,
                    attributes=[string_attribute("db.client.connection.state", "idle")],
                ),
            ]
        ),
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=resource_for(
                    "nexovue-poller",
                    "0.1.0",
                    "python",
                    extra=[
                        string_attribute("server.address", "orders-db.internal"),
                        int_attribute("server.port", 5432),
                    ],
                ),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.pollers.postgres", version="0.1.0"
                        ),
                        metrics=[mean_time, connection_count],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_POLLER,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000008",
        received_offset_millis=60_210,
        payload=request.SerializeToString(),
    )


FIXTURE_BUILDERS = {
    "door1-traces.bin": door1_traces,
    "door1-logs.bin": door1_logs,
    "door1-metrics.bin": door1_metrics,
    "door1-profiles.bin": door1_profiles,
    "door2-synthetic-api.bin": door2_synthetic_api,
    "door2-synthetic-evidence.bin": door2_synthetic_evidence,
    "door3-rum-logs.bin": door3_rum_logs,
    "door3-rum-traces.bin": door3_rum_traces,
    "door4b-cloudwatch.bin": door4b_cloudwatch,
    "door4b-dbstats.bin": door4b_dbstats,
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for filename, build in FIXTURE_BUILDERS.items():
        # deterministic=True so map ordering can never make a rebuild look like a change.
        encoded = build().SerializeToString(deterministic=True)
        (FIXTURES_DIR / filename).write_bytes(encoded)
        sys.stdout.write(f"{filename}: {len(encoded)} bytes\n")


if __name__ == "__main__":
    main()
