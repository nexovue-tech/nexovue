"""Door 2 produces the envelope the frozen contract describes (P1-34).

Every expectation here is read out of
`packages/contracts/fixtures/door2-synthetic-http.bin` rather than written down
a second time. Hardcoding the metric names would let this file and the contract
drift apart while both looked correct; deriving them means a change to the
contract fails this test, which is the point of freezing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

from nexovue_scheduler.probes.result import PHASE_ORDER, Certificate, ProbeResult
from nexovue_scheduler.publish import SEMCONV_VERSION, build_envelope

FIXTURE: Final = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "door2-synthetic-http.bin"
)

ORG_ID: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
OBSERVED_AT: Final = 1_787_649_300_000_000_000


def _metrics_of(envelope_bytes: bytes) -> metrics_service_pb2.ExportMetricsServiceRequest:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(envelope_bytes)
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(envelope.payload)
    return request


def _names_and_units(
    request: metrics_service_pb2.ExportMetricsServiceRequest,
) -> set[tuple[str, str]]:
    return {
        (metric.name, metric.unit)
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def _resource_keys(request: metrics_service_pb2.ExportMetricsServiceRequest) -> set[str]:
    return {
        attribute.key
        for resource_metrics in request.resource_metrics
        for attribute in resource_metrics.resource.attributes
    }


@pytest.fixture(scope="module")
def contract() -> metrics_service_pb2.ExportMetricsServiceRequest:
    """The frozen example of this door, which is the authority on its shape."""
    return _metrics_of(FIXTURE.read_bytes())


@pytest.fixture
def result() -> ProbeResult:
    """A healthy HTTPS check, shaped like a real one."""
    return ProbeResult(
        check_id="01996d5e-9a40-7d01-b000-000000000101",
        check_type="http",
        target="https://shop.acme.example/health",
        up=True,
        status_code=200,
        total_seconds=0.2841,
        phases={"dns": 0.0121, "tcp": 0.0334, "tls": 0.0908, "ttfb": 0.1302, "transfer": 0.0176},
        certificate=Certificate(
            days_remaining=47,
            issuer="CN=E6,O=Let's Encrypt,C=US",
            server_address="shop.acme.example",
        ),
    )


@pytest.fixture
def built(result: ProbeResult) -> bytes:
    return build_envelope(
        result,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        observed_at_unix_nano=OBSERVED_AT,
        environment="production",
        region="eu-west-1",
    )


def test_the_envelope_declares_the_synthetic_door_and_the_metrics_signal(built: bytes) -> None:
    """The routing fields the broker and the worker read."""
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(built)

    assert envelope.door == envelope_pb2.DOOR_SYNTHETIC
    assert envelope.signal == envelope_pb2.SIGNAL_METRICS
    assert envelope.tenant_id == ORG_ID
    assert envelope.project_id == PROJECT_ID
    assert envelope.semconv_version == SEMCONV_VERSION
    assert envelope.ingest_id, "an envelope with no ingest id cannot be traced through the pipeline"


def test_the_metrics_match_the_names_and_units_the_contract_froze(
    built: bytes, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    """A healthy check with a certificate emits exactly the fixture's metrics."""
    assert _names_and_units(_metrics_of(built)) == _names_and_units(contract)


def test_the_schema_url_matches_the_contract_on_both_levels(
    built: bytes, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    """The version whose attribute meanings this payload is written against.

    Compared explicitly because the key-set assertions above cannot see it —
    they passed for a payload that declared no schema at all, which is how this
    was missed in the first place.
    """
    mine, theirs = _metrics_of(built), contract
    assert [rm.schema_url for rm in mine.resource_metrics] == [
        rm.schema_url for rm in theirs.resource_metrics
    ]
    assert [sm.schema_url for rm in mine.resource_metrics for sm in rm.scope_metrics] == [
        sm.schema_url for rm in theirs.resource_metrics for sm in rm.scope_metrics
    ]


def test_the_resource_says_who_measured_and_from_where(
    built: bytes, contract: metrics_service_pb2.ExportMetricsServiceRequest
) -> None:
    """Same resource keys as the contract, including the region.

    The region is load-bearing rather than decoration: the same check answers
    differently from different places, and two readings cannot be compared
    without knowing where each was taken.
    """
    assert _resource_keys(_metrics_of(built)) == _resource_keys(contract)


def test_the_phase_durations_sum_to_the_reported_duration(built: bytes) -> None:
    """The breakdown has to account for the whole request.

    A phase set that does not add up to the total is worse than no breakdown:
    it invites someone to conclude time went missing somewhere.
    """
    request = _metrics_of(built)
    metrics = {
        metric.name: metric
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }

    phases = metrics["synthetic.check.phase.duration"].gauge.data_points
    total = metrics["synthetic.check.duration"].gauge.data_points[0].as_double

    assert {point.attributes[0].value.string_value for point in phases} == set(PHASE_ORDER)
    assert sum(point.as_double for point in phases) == pytest.approx(total, abs=1e-9)


def test_a_failed_check_is_still_published(result: ProbeResult) -> None:
    """Down is a measurement. Publishing nothing would be indistinguishable
    from a check that never ran, which is the ambiguity this product exists to
    remove."""
    down = ProbeResult(
        check_id=result.check_id,
        check_type="http",
        target=result.target,
        up=False,
        status_code=None,
        total_seconds=0.5696,
        phases={"dns": 0.3178, "tcp": 0.2518, "tls": 0.0, "ttfb": 0.0, "transfer": 0.0},
        error="(60, 'SSL certificate problem')",
    )
    request = _metrics_of(
        build_envelope(
            down,
            org_id=ORG_ID,
            project_id=PROJECT_ID,
            observed_at_unix_nano=OBSERVED_AT,
            environment="production",
            region="eu-west-1",
        )
    )

    names = {name for name, _ in _names_and_units(request)}
    assert "synthetic.check.status" in names
    # No certificate was seen, so no expiry is claimed. Reporting zero days
    # remaining would read as "expires today" rather than "not measured".
    assert "synthetic.tls.certificate.remaining" not in names

    status = next(
        metric
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == "synthetic.check.status"
    )
    point = status.gauge.data_points[0]
    assert point.as_int == 0
    # The request never got a response, so there is no status code to report.
    # A zero would chart as though it were one.
    assert not point.attributes
