"""Regenerate the golden fixtures in ../fixtures.

One committed envelope per door, per docs/architecture/ingestion-envelope.md. Every
value here is fixed — no clocks, no randomness — so a rebuild that changes a byte
means the contract or the payload shape changed, and the diff says which.

Run: uv run --no-project --with protobuf --with opentelemetry-proto \
         python packages/contracts/tools/build_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

CONTRACTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CONTRACTS_ROOT / "gen" / "python"))

from nexovue.v1 import envelope_pb2  # noqa: E402
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


def door2_synthetic_http() -> envelope_pb2.Envelope:
    """An uptime check: pass/fail, total duration, per-phase breakdown, certificate expiry."""
    observed_at = BASE_UNIX_NANO
    check_id = "01996d5e-9a40-7d01-b000-000000000101"

    def phase(step: str, seconds: float) -> metrics_pb2.NumberDataPoint:
        return metrics_pb2.NumberDataPoint(
            time_unix_nano=observed_at,
            as_double=seconds,
            attributes=[string_attribute("synthetic.check.step", step)],
        )

    status = synthetic_gauge(
        "synthetic.check.status",
        "1",
        "1 when the check passed, 0 when it failed",
        [
            metrics_pb2.NumberDataPoint(
                time_unix_nano=observed_at,
                as_int=1,
                attributes=[int_attribute("http.response.status_code", 200)],
            )
        ],
    )
    duration = synthetic_gauge(
        "synthetic.check.duration",
        "s",
        "Wall-clock duration of the whole check",
        [metrics_pb2.NumberDataPoint(time_unix_nano=observed_at, as_double=0.2841)],
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
                    string_attribute("tls.server.issuer", "CN=E6,O=Let's Encrypt,C=US"),
                    string_attribute("server.address", "shop.acme.example"),
                ],
            )
        ],
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=synthetic_resource(check_id, "http", "eu-west-1"),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.checks.http", version="0.1.0"
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


def door2_synthetic_browser() -> envelope_pb2.Envelope:
    """The failure detail a browser flow emits alongside its metrics.

    Door 2 produces two shapes (system-architecture.md): check-result metrics, and a
    log-style record carrying the error and its screenshot when a step fails. This is
    the second shape — a door that speaks no OTLP natively, emitting OTLP logs.
    """
    observed_at = BASE_UNIX_NANO
    check_id = "01996d5e-9a40-7d01-b000-000000000102"

    failure = logs_pb2.LogRecord(
        time_unix_nano=observed_at,
        observed_time_unix_nano=observed_at + 12 * NANOS_PER_MILLISECOND,
        severity_number=logs_pb2.SEVERITY_NUMBER_ERROR,
        severity_text="ERROR",
        body=common_pb2.AnyValue(
            string_value=(
                "step 'confirm-order' timed out after 30000ms waiting for "
                "locator('[data-test=order-confirmation]')"
            )
        ),
        attributes=[
            string_attribute("error.type", "TimeoutError"),
            string_attribute("synthetic.check.failed_step", "confirm-order"),
            int_attribute("synthetic.check.failed_step.index", 4),
            # Screenshots are too large for the pipeline; the blob lives in object storage.
            string_attribute(
                "synthetic.check.screenshot.ref",
                "s3://nexovue-synthetics/01996d5e-9a40-7d01-b000-000000000102/"
                "2026-08-26T09-15-00Z.png",
            ),
            string_attribute("url.full", "https://shop.acme.example/checkout"),
            string_attribute("browser.name", "chromium"),
            string_attribute("browser.version", "142.0.7444.51"),
        ],
    )

    completed = logs_pb2.LogRecord(
        time_unix_nano=observed_at + 40 * NANOS_PER_MILLISECOND,
        observed_time_unix_nano=observed_at + 41 * NANOS_PER_MILLISECOND,
        severity_number=logs_pb2.SEVERITY_NUMBER_INFO,
        severity_text="INFO",
        body=common_pb2.AnyValue(string_value="browser flow finished: 3 of 4 steps passed"),
        attributes=[
            int_attribute("synthetic.check.steps.total", 4),
            int_attribute("synthetic.check.steps.passed", 3),
            double_attribute("synthetic.check.duration", 32.104),
        ],
    )

    request = logs_service_pb2.ExportLogsServiceRequest(
        resource_logs=[
            logs_pb2.ResourceLogs(
                resource=synthetic_resource(check_id, "browser", "eu-west-1"),
                scope_logs=[
                    logs_pb2.ScopeLogs(
                        scope=common_pb2.InstrumentationScope(
                            name="nexovue.scheduler.checks.browser", version="0.1.0"
                        ),
                        log_records=[failure, completed],
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
        received_offset_millis=32_190,
        payload=request.SerializeToString(),
    )


# ----------------------------------------------------------------- door 3: RUM


def door3_rum() -> envelope_pb2.Envelope:
    """A browser page load: the document span, a fetch child carrying the backend trace, and LCP."""
    trace_id = bytes.fromhex("9c1f8e2d4a6b7c8d9e0f1a2b3c4d5e6f")
    document_span_id = bytes.fromhex("1a2b3c4d5e6f7a8b")
    fetch_span_id = bytes.fromhex("2b3c4d5e6f7a8b9c")
    start = BASE_UNIX_NANO

    document_load = trace_pb2.Span(
        trace_id=trace_id,
        span_id=document_span_id,
        name="documentLoad",
        kind=trace_pb2.Span.SPAN_KIND_INTERNAL,
        start_time_unix_nano=start,
        end_time_unix_nano=start + 1_940 * NANOS_PER_MILLISECOND,
        attributes=[
            string_attribute("url.full", "https://shop.acme.example/checkout"),
            string_attribute("url.path", "/checkout"),
            string_attribute(
                "user_agent.original",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
            ),
            string_attribute("session.id", "01996d5e-9a40-7e01-c000-0000000000aa"),
            double_attribute("browser.web_vital.lcp", 1.842),
            double_attribute("browser.web_vital.cls", 0.03),
            int_attribute("browser.web_vital.inp", 112),
        ],
        status=trace_pb2.Status(code=trace_pb2.Status.STATUS_CODE_UNSET),
    )

    # The backend correlation: this span's trace_id is what the server continues.
    checkout_fetch = trace_pb2.Span(
        trace_id=trace_id,
        span_id=fetch_span_id,
        parent_span_id=document_span_id,
        name="POST",
        kind=trace_pb2.Span.SPAN_KIND_CLIENT,
        start_time_unix_nano=start + 1_502 * NANOS_PER_MILLISECOND,
        end_time_unix_nano=start + 1_921 * NANOS_PER_MILLISECOND,
        attributes=[
            string_attribute("http.request.method", "POST"),
            string_attribute("url.full", "https://shop.acme.example/api/checkout"),
            int_attribute("http.response.status_code", 500),
            string_attribute("error.type", "500"),
        ],
        status=trace_pb2.Status(
            code=trace_pb2.Status.STATUS_CODE_ERROR, message="Internal Server Error"
        ),
    )

    request = trace_service_pb2.ExportTraceServiceRequest(
        resource_spans=[
            trace_pb2.ResourceSpans(
                resource=resource_for(
                    "acme-storefront",
                    "2026.8.14",
                    "webjs",
                    extra=[
                        string_attribute("browser.brands", "Safari 18.5"),
                        string_attribute("browser.platform", "iOS"),
                        bool_attribute("browser.mobile", True),
                    ],
                ),
                scope_spans=[
                    trace_pb2.ScopeSpans(
                        scope=common_pb2.InstrumentationScope(
                            name="@opentelemetry/instrumentation-document-load",
                            version="0.50.0",
                        ),
                        spans=[document_load, checkout_fetch],
                        schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                    )
                ],
                schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
            )
        ]
    )
    return envelope(
        envelope_pb2.DOOR_RUM,
        envelope_pb2.SIGNAL_SPANS,
        "01996d5e-9a40-7c01-a000-000000000006",
        received_offset_millis=2_050,
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
    "door2-synthetic-http.bin": door2_synthetic_http,
    "door2-synthetic-browser.bin": door2_synthetic_browser,
    "door3-rum.bin": door3_rum,
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
