"""Turn a probe result into the envelope every door produces, and publish it.

This file is where door 2 meets the invariant that *every door normalizes to one
internal envelope before the broker*. A probe result is OTLP metrics inside a
`nexovue.v1.Envelope` on `metrics-raw`, which means the existing metric-ingest
pipeline stores it with no new table and no new worker.

The shape is not invented here. `packages/contracts/fixtures/
door2-synthetic-http.bin` is the frozen example of this door, and the metric
names, units and attribute keys below are read from it rather than chosen. If
this file and that fixture disagree, the fixture is right.
"""

from __future__ import annotations

import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Final

from confluent_kafka import Producer
from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2

from nexovue_scheduler.probes.result import ProbeResult
from nexovue_scheduler.settings import Settings

# Frozen with the fixture. Changing it is a contract change, not a detail.
SEMCONV_VERSION: Final = "1.44.0"
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
    door: int,
    signal: int,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
) -> bytes:
    """Put an encoded OTLP payload inside the envelope every door produces.

    Shared by every door this service serves — HTTP checks, browser flows and
    pollers — because the invariant is that they all reach the broker in the
    same shape. A second copy of this would be a second definition of that
    shape, which is how doors drift apart.
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
    result: ProbeResult,
    *,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
    environment: str,
    region: str,
) -> bytes:
    """Encode one HTTP check result as a serialized `nexovue.v1.Envelope`."""
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.resource.CopyFrom(
        prober_resource(
            check_id=result.check_id,
            check_type=result.check_type,
            environment=environment,
            region=region,
        )
    )

    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = f"nexovue.scheduler.checks.{result.check_type}"
    scope_metrics.scope.version = PROBER_VERSION
    _add_metrics(scope_metrics, result, observed_at_unix_nano)

    return wrap_envelope(
        request.SerializeToString(),
        door=envelope_pb2.DOOR_SYNTHETIC,
        signal=envelope_pb2.SIGNAL_METRICS,
        org_id=org_id,
        project_id=project_id,
        observed_at_unix_nano=observed_at_unix_nano,
    )


def _add_metrics(
    scope_metrics: metrics_pb2.ScopeMetrics,
    result: ProbeResult,
    at_unix_nano: int,
) -> None:
    """Every metric this check produced.

    All gauges. A check is a reading taken at a moment, not a quantity that
    accumulates between readings, so a sum would be the wrong shape and would
    make a rate out of something that has none.
    """
    status = scope_metrics.metrics.add(name=METRIC_STATUS, unit=UNIT_RATIO)
    status_point = status.gauge.data_points.add(
        time_unix_nano=at_unix_nano, as_int=1 if result.up else 0
    )
    if result.status_code is not None:
        # Absent rather than zero when the request never got a response: a
        # status of 0 would sort and chart as if it were real.
        status_point.attributes.append(
            common_pb2.KeyValue(
                key=ATTR_STATUS_CODE,
                value=common_pb2.AnyValue(int_value=result.status_code),
            )
        )

    duration = scope_metrics.metrics.add(name=METRIC_DURATION, unit=UNIT_SECONDS)
    duration.gauge.data_points.add(time_unix_nano=at_unix_nano, as_double=result.total_seconds)

    phases = scope_metrics.metrics.add(name=METRIC_PHASE_DURATION, unit=UNIT_SECONDS)
    for step, seconds in result.phases.items():
        point = phases.gauge.data_points.add(time_unix_nano=at_unix_nano, as_double=seconds)
        point.attributes.append(
            common_pb2.KeyValue(key=ATTR_STEP, value=common_pb2.AnyValue(string_value=step))
        )

    if result.certificate is not None:
        remaining = scope_metrics.metrics.add(name=METRIC_CERT_REMAINING, unit=UNIT_DAYS)
        point = remaining.gauge.data_points.add(
            time_unix_nano=at_unix_nano, as_int=result.certificate.days_remaining
        )
        point.attributes.extend(
            [
                common_pb2.KeyValue(
                    key=ATTR_ISSUER,
                    value=common_pb2.AnyValue(string_value=result.certificate.issuer),
                ),
                common_pb2.KeyValue(
                    key=ATTR_SERVER_ADDRESS,
                    value=common_pb2.AnyValue(string_value=result.certificate.server_address),
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
