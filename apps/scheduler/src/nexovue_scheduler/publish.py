"""Turn a probe result into the envelope every door produces, and publish it.

This file is where door 2 meets the invariant that *every door normalizes to one
internal envelope before the broker*. A probe result is OTLP metrics inside a
`nexovue.v1.Envelope` on `metrics-raw`, which means the existing metric-ingest
pipeline stores it with no new table and no new worker.

The shape is not invented here. `packages/contracts/fixtures/
door2-synthetic-api.bin` and `door2-synthetic-evidence.bin` are the frozen
examples of this door, and the metric names, units and attribute keys below are
read from them rather than chosen. If this file and a fixture disagree, the
fixture is right.
"""

from __future__ import annotations

import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Final

from confluent_kafka import Producer
from nexovue.v1 import envelope_pb2
from nexovue_platform.checks.evidence import RunEvidence
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.logs.v1 import logs_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2

from nexovue_scheduler.probes.result import RunMeasurement
from nexovue_scheduler.settings import Settings

# Frozen with the fixture. Changing it is a contract change, not a detail.
SEMCONV_VERSION: Final = "1.44.0"

# The semantic-convention schema every door 2 and 4b payload declares, on both
# the resource and the scope. A reader uses it to know how to interpret the
# attribute names; without it a consumer has to guess which version's meanings
# apply. Omitted here until a subagent noticed the frozen fixture carries it and
# this did not — the test compared attribute *keys* and never looked.
SCHEMA_URL: Final = f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}"
PROBER_SERVICE_NAME: Final = "nexovue-prober"
PROBER_VERSION: Final = "0.1.0"


# We encode OTLP directly rather than through the OpenTelemetry SDK, so the
# honest thing to report as the SDK version is the proto library we encode with.
# Read rather than hardcoded: a version that has to be remembered on upgrade is
# a version that will be wrong.
def _proto_version() -> str:
    try:
        return version("opentelemetry-proto")
    except PackageNotFoundError:  # pragma: no cover - only if the dep is missing
        return "unknown"


OTLP_PROTO_VERSION: Final = _proto_version()

METRIC_STATUS: Final = "synthetic.check.status"
METRIC_DURATION: Final = "synthetic.check.duration"
METRIC_PHASE_DURATION: Final = "synthetic.check.phase.duration"
METRIC_CERT_REMAINING: Final = "synthetic.tls.certificate.remaining"

ATTR_STEP: Final = "synthetic.check.step"
# Every point of a run carries these, so uptime can count only confirmed,
# measured runs and a retry stays visible without being counted (design,
# *Results*). The run id makes every run its own series — the price of joining a
# run's reading to its evidence. The check page reads by check and window, so it
# is not paid there; a series count over prober metrics does pay it.
ATTR_OUTCOME: Final = "synthetic.check.outcome"
ATTR_RUN_ID: Final = "synthetic.check.run.id"
ATTR_ATTEMPT: Final = "synthetic.check.attempt"
ATTR_FINAL: Final = "synthetic.check.final"
ATTR_MAINTENANCE: Final = "synthetic.check.maintenance"
ATTR_LOCATION: Final = "synthetic.check.location"
ATTR_STATUS_CODE: Final = "http.response.status_code"
ATTR_ISSUER: Final = "tls.server.issuer"
ATTR_SERVER_ADDRESS: Final = "server.address"

UNIT_RATIO: Final = "1"
UNIT_SECONDS: Final = "s"
UNIT_DAYS: Final = "d"

# How long a probe waits for the broker to confirm its result. A check whose
# measurement never reached Kafka is a blind minute, so the task fails loudly
# and Celery retries rather than reporting success on a dropped result.
DELIVERY_TIMEOUT_SECONDS: Final = 10


class PublishError(RuntimeError):
    """The broker did not accept the result, so the check did not really run."""


def wrap_envelope(
    payload: bytes,
    *,
    door: envelope_pb2.Door,
    signal: envelope_pb2.Signal,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
) -> bytes:
    """Put an encoded OTLP payload inside the envelope every door produces.

    Shared by every door this service serves — checks and pollers — because
    the invariant is that they all reach the broker in the same shape. A second
    copy of this would be a second definition of that shape, which is how doors
    drift apart.
    """
    envelope = envelope_pb2.Envelope(
        tenant_id=org_id,
        project_id=project_id,
        door=door,
        signal=signal,
        received_at_unix_nano=observed_at_unix_nano,
        # The receiver stamps a UUIDv7 here. Python's uuid module has no v7
        # before 3.14, and nothing downstream orders on this value — it
        # identifies a batch in a log line — so v4 is enough.
        ingest_id=str(uuid.uuid4()),
        semconv_version=SEMCONV_VERSION,
        payload=payload,
    )
    return envelope.SerializeToString()


def prober_resource(
    *, check_id: str, check_type: str, environment: str, region: str
) -> resource_pb2.Resource:
    """Who took a measurement, and from where.

    Every door 2 signal carries this, metrics and logs alike, and the frozen
    fixtures for both use the same keys.

    `cloud.region` is part of the reading, not deployment trivia: the same check
    answers differently from different places, and two readings cannot be
    compared without knowing where each was taken.
    """
    resource = resource_pb2.Resource()
    for key, value in (
        ("service.name", PROBER_SERVICE_NAME),
        ("service.version", PROBER_VERSION),
        ("deployment.environment.name", environment),
        ("telemetry.sdk.name", "opentelemetry"),
        ("telemetry.sdk.language", "python"),
        ("telemetry.sdk.version", OTLP_PROTO_VERSION),
        ("synthetic.check.id", check_id),
        ("synthetic.check.type", check_type),
        ("cloud.region", region),
    ):
        resource.attributes.append(
            common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(string_value=value))
        )
    return resource


def build_envelope(
    measurement: RunMeasurement,
    *,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
    environment: str,
    region: str,
) -> bytes:
    """Encode one run's numbers as a serialized `nexovue.v1.Envelope`."""
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.schema_url = SCHEMA_URL
    resource_metrics.resource.CopyFrom(
        prober_resource(
            check_id=measurement.check_id,
            check_type=measurement.check_type,
            environment=environment,
            region=region,
        )
    )

    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.schema_url = SCHEMA_URL
    scope_metrics.scope.name = f"nexovue.scheduler.checks.{measurement.check_type}"
    scope_metrics.scope.version = PROBER_VERSION
    _add_metrics(scope_metrics, measurement, observed_at_unix_nano)

    return wrap_envelope(
        request.SerializeToString(),
        door=envelope_pb2.DOOR_SYNTHETIC,
        signal=envelope_pb2.SIGNAL_METRICS,
        org_id=org_id,
        project_id=project_id,
        observed_at_unix_nano=observed_at_unix_nano,
    )


def build_evidence_envelope(
    evidence: RunEvidence,
    *,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
    environment: str,
    region: str,
) -> bytes:
    """Encode what one run sent and received as an OTLP log record (FR31).

    The body is the evidence as JSON — already redacted, already size-capped — and
    the attributes are the ones a reader finds it by.
    """
    record = logs_pb2.LogRecord(
        time_unix_nano=observed_at_unix_nano,
        observed_time_unix_nano=observed_at_unix_nano,
        severity_number=_SEVERITY[evidence.outcome][0],
        severity_text=_SEVERITY[evidence.outcome][1],
        body=common_pb2.AnyValue(string_value=evidence.model_dump_json()),
        attributes=_run_attributes(
            outcome=evidence.outcome,
            run_id=evidence.run_id,
            attempt=evidence.attempt,
            final=evidence.final,
            maintenance=evidence.maintenance,
            location=evidence.location,
        ),
    )
    request = logs_service_pb2.ExportLogsServiceRequest(
        resource_logs=[
            logs_pb2.ResourceLogs(
                resource=prober_resource(
                    check_id=evidence.check_id or "",
                    check_type=evidence.check_type,
                    environment=environment,
                    region=region,
                ),
                scope_logs=[
                    logs_pb2.ScopeLogs(
                        scope=common_pb2.InstrumentationScope(
                            name=f"nexovue.scheduler.checks.{evidence.check_type}",
                            version=PROBER_VERSION,
                        ),
                        log_records=[record],
                        schema_url=SCHEMA_URL,
                    )
                ],
                schema_url=SCHEMA_URL,
            )
        ]
    )
    return wrap_envelope(
        request.SerializeToString(),
        door=envelope_pb2.DOOR_SYNTHETIC,
        signal=envelope_pb2.SIGNAL_LOGS,
        org_id=org_id,
        project_id=project_id,
        observed_at_unix_nano=observed_at_unix_nano,
    )


# A down run is an error; a run that could not be judged is a warning, because
# someone has to fix something but the customer's service may be fine.
_SEVERITY: Final = {
    "up": (logs_pb2.SEVERITY_NUMBER_INFO, "INFO"),
    "degraded": (logs_pb2.SEVERITY_NUMBER_WARN, "WARN"),
    "down": (logs_pb2.SEVERITY_NUMBER_ERROR, "ERROR"),
    "misconfigured": (logs_pb2.SEVERITY_NUMBER_WARN, "WARN"),
    "prober_error": (logs_pb2.SEVERITY_NUMBER_WARN, "WARN"),
}


def _run_attributes(
    *, outcome: str, run_id: str, attempt: int, final: bool, maintenance: bool, location: str
) -> list[common_pb2.KeyValue]:
    return [
        common_pb2.KeyValue(key=ATTR_OUTCOME, value=common_pb2.AnyValue(string_value=outcome)),
        common_pb2.KeyValue(key=ATTR_RUN_ID, value=common_pb2.AnyValue(string_value=run_id)),
        common_pb2.KeyValue(key=ATTR_ATTEMPT, value=common_pb2.AnyValue(int_value=attempt)),
        common_pb2.KeyValue(key=ATTR_FINAL, value=common_pb2.AnyValue(bool_value=final)),
        common_pb2.KeyValue(
            key=ATTR_MAINTENANCE, value=common_pb2.AnyValue(bool_value=maintenance)
        ),
        common_pb2.KeyValue(key=ATTR_LOCATION, value=common_pb2.AnyValue(string_value=location)),
    ]


def _add_metrics(
    scope_metrics: metrics_pb2.ScopeMetrics,
    measurement: RunMeasurement,
    at_unix_nano: int,
) -> None:
    """Every metric this run produced.

    All gauges. A check is a reading taken at a moment, not a quantity that
    accumulates between readings, so a sum would be the wrong shape and would
    make a rate out of something that has none.
    """
    run = _run_attributes(
        outcome=measurement.outcome,
        run_id=measurement.run_id,
        attempt=measurement.attempt,
        final=measurement.final,
        maintenance=measurement.maintenance,
        location=measurement.location,
    )

    status = scope_metrics.metrics.add(name=METRIC_STATUS, unit=UNIT_RATIO)
    status_point = status.gauge.data_points.add(
        time_unix_nano=at_unix_nano, as_int=1 if measurement.up else 0
    )
    status_point.attributes.extend(run)
    if measurement.status_code is not None:
        # Absent rather than zero when the request never got a response: a
        # status of 0 would sort and chart as if it were real.
        status_point.attributes.append(
            common_pb2.KeyValue(
                key=ATTR_STATUS_CODE,
                value=common_pb2.AnyValue(int_value=measurement.status_code),
            )
        )

    if measurement.total_seconds is not None:
        # No point at all when nothing was timed — a heartbeat is not probed, and a
        # misconfigured run never sent a request. A zero would drag every mean and
        # percentile down as if the target had answered instantly.
        duration = scope_metrics.metrics.add(name=METRIC_DURATION, unit=UNIT_SECONDS)
        duration_point = duration.gauge.data_points.add(
            time_unix_nano=at_unix_nano, as_double=measurement.total_seconds
        )
        duration_point.attributes.extend(run)

    if measurement.phases:
        phases = scope_metrics.metrics.add(name=METRIC_PHASE_DURATION, unit=UNIT_SECONDS)
        for step, seconds in measurement.phases.items():
            point = phases.gauge.data_points.add(time_unix_nano=at_unix_nano, as_double=seconds)
            point.attributes.extend(run)
            point.attributes.append(
                common_pb2.KeyValue(key=ATTR_STEP, value=common_pb2.AnyValue(string_value=step))
            )

    if measurement.certificate is not None:
        remaining = scope_metrics.metrics.add(name=METRIC_CERT_REMAINING, unit=UNIT_DAYS)
        point = remaining.gauge.data_points.add(
            time_unix_nano=at_unix_nano, as_int=measurement.certificate.days_remaining
        )
        point.attributes.extend(run)
        point.attributes.extend(
            [
                common_pb2.KeyValue(
                    key=ATTR_ISSUER,
                    value=common_pb2.AnyValue(string_value=measurement.certificate.issuer),
                ),
                common_pb2.KeyValue(
                    key=ATTR_SERVER_ADDRESS,
                    value=common_pb2.AnyValue(string_value=measurement.certificate.server_address),
                ),
            ]
        )


class ResultPublisher:
    """Publishes probe results to Kafka, one producer per worker process."""

    def __init__(self, settings: Settings, producer: Producer | None = None) -> None:
        self._settings = settings
        self._producer = producer or Producer(
            {
                "bootstrap.servers": ",".join(settings.kafka_brokers),
                # A measurement is worth nothing if it is not durable, and a
                # probe runs again in a minute anyway — so wait for the full
                # in-sync set rather than trading durability for latency.
                "acks": "all",
                "enable.idempotence": True,
                "client.id": f"{PROBER_SERVICE_NAME}-{uuid.uuid4().hex[:8]}",
            }
        )

    def publish_many(self, messages: list[tuple[str, str, bytes]]) -> None:
        """Send (topic, org_id, envelope) messages and wait once for all of them.

        One flush for a batch: a sweep that flushed per heartbeat would spend its
        time limit waiting on the broker, one message at a time.
        """
        for destination, org_id, envelope in messages:
            self._producer.produce(destination, key=org_id, value=envelope)
        undelivered = self._producer.flush(DELIVERY_TIMEOUT_SECONDS)
        if undelivered:
            raise PublishError(
                f"{undelivered} of {len(messages)} probe result(s) not acknowledged "
                f"within {DELIVERY_TIMEOUT_SECONDS}s"
            )

    def publish(self, envelope: bytes, *, org_id: str, topic: str | None = None) -> None:
        """Send one envelope and wait for the broker to confirm it.

        Keyed by organization, matching how the receiver keys logs and metrics,
        so one tenant's readings keep their order relative to each other.

        Blocks until delivery is confirmed. A probe that returned success while
        its result sat undelivered in a buffer would be reporting that a check
        ran when nothing can see the result.
        """
        destination = topic or self._settings.kafka_metrics_topic
        self._producer.produce(destination, key=org_id, value=envelope)
        undelivered = self._producer.flush(DELIVERY_TIMEOUT_SECONDS)
        if undelivered:
            raise PublishError(
                f"{undelivered} probe result(s) not acknowledged by "
                f"{destination} within {DELIVERY_TIMEOUT_SECONDS}s"
            )
