"""Door 2 produces the envelopes the frozen contract describes.

Every expectation here is read out of `packages/contracts/fixtures/
door2-synthetic-api.bin` and `door2-synthetic-evidence.bin` rather than written
down a second time. Hardcoding the metric names would let this file and the
contract drift apart while both looked correct; deriving them means a change to
the contract fails this test, which is the point of freezing it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from nexovue.v1 import envelope_pb2
from nexovue_platform.checks.evidence import RunEvidence
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2

from nexovue_scheduler.probes.result import PHASE_ORDER, Certificate, RunMeasurement
from nexovue_scheduler.publish import SEMCONV_VERSION, build_envelope, build_evidence_envelope

FIXTURES: Final = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"

ORG_ID: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
OBSERVED_AT: Final = 1_787_649_300_000_000_000
RUN_KEYS: Final = {
    "synthetic.check.outcome",
    "synthetic.check.run.id",
    "synthetic.check.attempt",
    "synthetic.check.final",
    "synthetic.check.maintenance",
    "synthetic.check.location",
}


def _metrics_of(envelope_bytes: bytes) -> metrics_service_pb2.ExportMetricsServiceRequest:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(envelope_bytes)
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(envelope.payload)
    return request


def _logs_of(envelope_bytes: bytes) -> logs_service_pb2.ExportLogsServiceRequest:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(envelope_bytes)
    request = logs_service_pb2.ExportLogsServiceRequest()
    request.ParseFromString(envelope.payload)
    return request


def _metrics(request: metrics_service_pb2.ExportMetricsServiceRequest) -> list[metrics_pb2.Metric]:
    return [
        metric
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    ]


def _resource_keys(request: metrics_service_pb2.ExportMetricsServiceRequest) -> set[str]:
    return {
        attribute.key
        for resource_metrics in request.resource_metrics
        for attribute in resource_metrics.resource.attributes
    }


@pytest.fixture(scope="module")
def contract() -> metrics_service_pb2.ExportMetricsServiceRequest:
    return _metrics_of((FIXTURES / "door2-synthetic-api.bin").read_bytes())


@pytest.fixture
def measurement() -> RunMeasurement:
    """A healthy HTTPS API check, shaped like a real one."""
    return RunMeasurement(
        check_id="01996d5e-9a40-7d01-b000-000000000101",
        check_type="api",
        run_id="3f9a1c52-7e0b-4d8a-9f61-2c4e8b7a1d05",
        outcome="up",
        attempt=1,
        final=True,
        maintenance=False,
        location="mumbai",
        total_seconds=0.2841,
        status_code=200,
        phases={"dns": 0.0121, "tcp": 0.0334, "tls": 0.0908, "ttfb": 0.1302, "transfer": 0.0176},
        certificate=Certificate(
            days_remaining=47,
            issuer="CN=E6,O=Let's Encrypt,C=US",
            server_address="shop.acme.example",
        ),
    )


def _build(measurement: RunMeasurement) -> bytes:
    return build_envelope(
        measurement,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        observed_at_unix_nano=OBSERVED_AT,
        environment="production",
        region="mumbai",
    )


def test_the_envelope_declares_the_synthetic_door_and_the_metrics_signal(
    measurement: RunMeasurement,
) -> None:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(_build(measurement))

    assert envelope.door == envelope_pb2.DOOR_SYNTHETIC
    assert envelope.signal == envelope_pb2.SIGNAL_METRICS
    assert envelope.tenant_id == ORG_ID
    assert envelope.project_id == PROJECT_ID
    assert envelope.semconv_version == SEMCONV_VERSION
    assert envelope.ingest_id, "an envelope with no ingest id cannot be traced through the pipeline"


def test_the_metrics_match_the_names_and_units_the_contract_froze(
    measurement: RunMeasurement, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    built = {(metric.name, metric.unit) for metric in _metrics(_metrics_of(_build(measurement)))}
    assert built == {(metric.name, metric.unit) for metric in _metrics(contract)}


def test_every_point_carries_the_attributes_the_contract_froze(
    measurement: RunMeasurement, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    """Uptime counts only confirmed runs, which it can only do if every point says
    whether it is one."""

    def keys(request: metrics_service_pb2.ExportMetricsServiceRequest) -> dict[str, set[str]]:
        return {
            metric.name: {attribute.key for attribute in metric.gauge.data_points[0].attributes}
            for metric in _metrics(request)
        }

    built = keys(_metrics_of(_build(measurement)))
    assert built == keys(contract)
    assert all(point_keys >= RUN_KEYS for point_keys in built.values())


def test_the_schema_url_and_resource_match_the_contract(
    measurement: RunMeasurement, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    mine = _metrics_of(_build(measurement))
    assert [rm.schema_url for rm in mine.resource_metrics] == [
        rm.schema_url for rm in contract.resource_metrics
    ]
    assert _resource_keys(mine) == _resource_keys(contract)


def test_the_phase_durations_sum_to_the_reported_duration(measurement: RunMeasurement) -> None:
    metrics = {metric.name: metric for metric in _metrics(_metrics_of(_build(measurement)))}
    phases = metrics["synthetic.check.phase.duration"].gauge.data_points
    total = metrics["synthetic.check.duration"].gauge.data_points[0].as_double

    steps = {
        attribute.value.string_value
        for point in phases
        for attribute in point.attributes
        if attribute.key == "synthetic.check.step"
    }
    assert steps == set(PHASE_ORDER)
    assert sum(point.as_double for point in phases) == pytest.approx(total, abs=1e-9)


def test_a_failed_run_is_still_published_without_a_status_code(
    measurement: RunMeasurement,
) -> None:
    """Down is a measurement. And a request that never got a response has no
    status code: a zero would chart as though it were one."""
    down = RunMeasurement(
        check_id=measurement.check_id,
        check_type="api",
        run_id=measurement.run_id,
        outcome="down",
        attempt=2,
        final=True,
        maintenance=False,
        location="mumbai",
        total_seconds=0.5696,
        phases={"dns": 0.3178, "tcp": 0.2518, "tls": 0.0, "ttfb": 0.0, "transfer": 0.0},
    )
    metrics = {metric.name: metric for metric in _metrics(_metrics_of(_build(down)))}

    assert "synthetic.tls.certificate.remaining" not in metrics
    point = metrics["synthetic.check.status"].gauge.data_points[0]
    assert point.as_int == 0
    assert {attribute.key for attribute in point.attributes} == RUN_KEYS


def test_a_run_that_timed_nothing_publishes_no_duration_point(
    measurement: RunMeasurement,
) -> None:
    """A heartbeat is not probed, and a misconfigured run never sent a request.

    A zero-second point would be averaged and percentiled as an instant answer,
    so the reading is absent rather than zero — the same rule as the status code.
    """
    untimed = RunMeasurement(
        check_id=measurement.check_id,
        check_type="heartbeat",
        run_id=measurement.run_id,
        outcome="down",
        attempt=1,
        final=True,
        maintenance=False,
        location="mumbai",
        total_seconds=None,
    )
    metrics = {metric.name: metric for metric in _metrics(_metrics_of(_build(untimed)))}

    assert "synthetic.check.duration" not in metrics
    # The run itself is still reported: silence is a reading.
    assert metrics["synthetic.check.status"].gauge.data_points[0].as_int == 0


def test_a_degraded_run_is_up_for_uptime_and_says_it_is_degraded(
    measurement: RunMeasurement,
) -> None:
    degraded = RunMeasurement(
        check_id=measurement.check_id,
        check_type="api",
        run_id=measurement.run_id,
        outcome="degraded",
        attempt=1,
        final=True,
        maintenance=False,
        location="mumbai",
        total_seconds=2.5,
    )
    point = {m.name: m for m in _metrics(_metrics_of(_build(degraded)))}[
        "synthetic.check.status"
    ].gauge.data_points[0]
    outcome = next(a for a in point.attributes if a.key == "synthetic.check.outcome")
    assert point.as_int == 1
    assert outcome.value.string_value == "degraded"


def test_the_evidence_record_matches_the_contract() -> None:
    """Same attribute keys, severity for a down run, and a body the API can read back."""
    contract = _logs_of((FIXTURES / "door2-synthetic-evidence.bin").read_bytes())
    frozen = contract.resource_logs[0].scope_logs[0].log_records[0]
    evidence = RunEvidence.model_validate_json(frozen.body.string_value)

    built = _logs_of(
        build_evidence_envelope(
            evidence,
            org_id=ORG_ID,
            project_id=PROJECT_ID,
            observed_at_unix_nano=OBSERVED_AT,
            environment="production",
            region="mumbai",
        )
    )
    record = built.resource_logs[0].scope_logs[0].log_records[0]

    assert {a.key for a in record.attributes} == {a.key for a in frozen.attributes}
    assert record.severity_text == frozen.severity_text == "ERROR"
    assert RunEvidence.model_validate_json(record.body.string_value) == evidence
    assert {a.key for a in built.resource_logs[0].resource.attributes} == {
        a.key for a in contract.resource_logs[0].resource.attributes
    }
    assert datetime(2026, 1, 1, tzinfo=UTC) < evidence.started_at
