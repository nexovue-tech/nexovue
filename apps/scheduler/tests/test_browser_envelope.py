"""Door 2's browser flow produces the envelope the frozen contract describes (P1-35).

Every expectation here is read out of
`packages/contracts/fixtures/door2-synthetic-browser.bin` rather than written
down a second time. Hardcoding the attribute keys would let this file and the
contract drift apart while both looked correct; deriving them means a change to
the contract fails this test, which is the point of freezing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.logs.v1 import logs_pb2

from nexovue_scheduler.probes.browser import (
    ERROR_TYPE_CONFIGURATION,
    FlowResult,
    StepFailure,
    build_envelope,
)
from nexovue_scheduler.publish import SEMCONV_VERSION

FIXTURE: Final = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "door2-synthetic-browser.bin"
)

ORG_ID: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
CHECK_ID: Final = "01996d5e-9a40-7d01-b000-000000000102"
OBSERVED_AT: Final = 1_787_649_300_000_000_000
FAILED_AT: Final = 1_787_649_300_012_000_000


def _logs_of(envelope_bytes: bytes) -> logs_service_pb2.ExportLogsServiceRequest:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(envelope_bytes)
    request = logs_service_pb2.ExportLogsServiceRequest()
    request.ParseFromString(envelope.payload)
    return request


def _records(
    request: logs_service_pb2.ExportLogsServiceRequest,
) -> list[logs_pb2.LogRecord]:
    return [
        record
        for resource_logs in request.resource_logs
        for scope_logs in resource_logs.scope_logs
        for record in scope_logs.log_records
    ]


def _record_with_severity(
    request: logs_service_pb2.ExportLogsServiceRequest, severity: int
) -> logs_pb2.LogRecord:
    matching = [record for record in _records(request) if record.severity_number == severity]
    assert len(matching) == 1, f"expected exactly one {severity} record, got {len(matching)}"
    return matching[0]


def _attribute_keys(record: logs_pb2.LogRecord) -> set[str]:
    return {attribute.key for attribute in record.attributes}


def _resource_keys(request: logs_service_pb2.ExportLogsServiceRequest) -> set[str]:
    return {
        attribute.key
        for resource_logs in request.resource_logs
        for attribute in resource_logs.resource.attributes
    }


def _scopes(request: logs_service_pb2.ExportLogsServiceRequest) -> set[tuple[str, str]]:
    return {
        (scope_logs.scope.name, scope_logs.scope.version)
        for resource_logs in request.resource_logs
        for scope_logs in resource_logs.scope_logs
    }


def _schema_urls(request: logs_service_pb2.ExportLogsServiceRequest) -> set[str]:
    urls = set()
    for resource_logs in request.resource_logs:
        urls.add(resource_logs.schema_url)
        for scope_logs in resource_logs.scope_logs:
            urls.add(scope_logs.schema_url)
    return urls


@pytest.fixture(scope="module")
def contract() -> logs_service_pb2.ExportLogsServiceRequest:
    """The frozen example of this door, which is the authority on its shape."""
    return _logs_of(FIXTURE.read_bytes())


@pytest.fixture
def failed_flow() -> FlowResult:
    """A checkout flow that timed out on its last step, shaped like a real one."""
    return FlowResult(
        check_id=CHECK_ID,
        target="https://shop.acme.example/checkout",
        steps_total=4,
        steps_passed=3,
        total_seconds=32.104,
        failure=StepFailure(
            name="confirm-order",
            index=4,
            error_type="TimeoutError",
            message=(
                "Timeout 30000ms exceeded waiting for locator('[data-test=order-confirmation]')"
            ),
            at_unix_nano=FAILED_AT,
            url="https://shop.acme.example/checkout",
            browser_version="142.0.7444.51",
            screenshot_ref=(f"s3://nexovue-synthetics/{CHECK_ID}/2026-08-26T09-15-00Z.png"),
        ),
    )


def _build(result: FlowResult) -> bytes:
    return build_envelope(
        result,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        observed_at_unix_nano=OBSERVED_AT,
        environment="production",
        region="eu-west-1",
    )


@pytest.fixture
def built(failed_flow: FlowResult) -> bytes:
    return _build(failed_flow)


def test_the_envelope_declares_the_synthetic_door_and_the_logs_signal(built: bytes) -> None:
    """The routing fields the broker and the worker read.

    Logs, not metrics: a flow's result is which step broke and what it said, and
    the metric pipeline has nowhere to put that.
    """
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(built)

    assert envelope.door == envelope_pb2.DOOR_SYNTHETIC
    assert envelope.signal == envelope_pb2.SIGNAL_LOGS
    assert envelope.tenant_id == ORG_ID
    assert envelope.project_id == PROJECT_ID
    assert envelope.semconv_version == SEMCONV_VERSION
    assert envelope.ingest_id, "an envelope with no ingest id cannot be traced through the pipeline"


def test_the_routing_fields_match_the_contracts_own(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """Door and signal read from the fixture rather than named here."""
    frozen = envelope_pb2.Envelope()
    frozen.ParseFromString(FIXTURE.read_bytes())
    ours = envelope_pb2.Envelope()
    ours.ParseFromString(built)

    assert (ours.door, ours.signal) == (frozen.door, frozen.signal)
    assert ours.semconv_version == frozen.semconv_version


def test_the_resource_says_who_measured_and_from_where(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """Same resource keys as the contract, including the region."""
    assert _resource_keys(_logs_of(built)) == _resource_keys(contract)


def test_the_scope_and_schema_url_match_the_contract(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """The scope names the check type, and both levels carry the semconv schema."""
    assert _scopes(_logs_of(built)) == _scopes(contract)
    assert _schema_urls(_logs_of(built)) == _schema_urls(contract)


def test_a_failed_flow_emits_the_two_records_the_contract_froze(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """One record per severity, exactly as the fixture has them."""
    ours = _logs_of(built)
    assert [record.severity_number for record in _records(ours)] == [
        record.severity_number for record in _records(contract)
    ]
    assert [record.severity_text for record in _records(ours)] == [
        record.severity_text for record in _records(contract)
    ]


def test_the_failure_record_carries_the_contracts_attributes(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """Which step failed, why, on what page, in which browser."""
    severity = logs_pb2.SEVERITY_NUMBER_ERROR
    ours = _record_with_severity(_logs_of(built), severity)
    frozen = _record_with_severity(contract, severity)

    assert _attribute_keys(ours) == _attribute_keys(frozen)
    assert ours.body.string_value, "a failure with no message is not a failure anyone can act on"


def test_the_failure_records_attribute_types_match_the_contract(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """A step index sent as a string would not sort or compare downstream."""
    severity = logs_pb2.SEVERITY_NUMBER_ERROR
    ours = {
        a.key: a.value.WhichOneof("value")
        for a in _record_with_severity(_logs_of(built), severity).attributes
    }
    frozen = {
        a.key: a.value.WhichOneof("value")
        for a in _record_with_severity(contract, severity).attributes
    }
    assert ours == frozen


def test_the_summary_record_carries_the_contracts_attributes(
    built: bytes, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """How many steps there were, how many passed, how long it took."""
    severity = logs_pb2.SEVERITY_NUMBER_INFO
    ours = _record_with_severity(_logs_of(built), severity)
    frozen = _record_with_severity(contract, severity)

    assert _attribute_keys(ours) == _attribute_keys(frozen)
    assert {a.key: a.value.WhichOneof("value") for a in ours.attributes} == {
        a.key: a.value.WhichOneof("value") for a in frozen.attributes
    }


def test_the_summary_counts_are_the_flows_own(built: bytes, failed_flow: FlowResult) -> None:
    """The numbers published are the numbers measured."""
    record = _record_with_severity(_logs_of(built), logs_pb2.SEVERITY_NUMBER_INFO)
    values = {a.key: a.value for a in record.attributes}

    assert values["synthetic.check.steps.total"].int_value == failed_flow.steps_total
    assert values["synthetic.check.steps.passed"].int_value == failed_flow.steps_passed
    assert values["synthetic.check.duration"].double_value == pytest.approx(
        failed_flow.total_seconds
    )


def test_a_healthy_flow_publishes_only_the_summary() -> None:
    """A run that says "4 of 4 passed" is what makes a missing run visible.

    Publishing nothing on success would make "nobody looked" indistinguishable
    from "nothing was wrong", which is the ambiguity this product removes.
    """
    healthy = FlowResult(
        check_id=CHECK_ID,
        target="https://shop.acme.example/checkout",
        steps_total=4,
        steps_passed=4,
        total_seconds=9.42,
    )
    records = _records(_logs_of(_build(healthy)))

    assert len(records) == 1
    assert records[0].severity_number == logs_pb2.SEVERITY_NUMBER_INFO


def test_a_screenshot_that_was_not_kept_is_omitted_not_empty(
    failed_flow: FlowResult, contract: logs_service_pb2.ExportLogsServiceRequest
) -> None:
    """An empty reference reads as a broken link rather than as "not captured"."""
    without = FlowResult(
        check_id=failed_flow.check_id,
        target=failed_flow.target,
        steps_total=failed_flow.steps_total,
        steps_passed=failed_flow.steps_passed,
        total_seconds=failed_flow.total_seconds,
        failure=StepFailure(
            name="confirm-order",
            index=4,
            error_type="TimeoutError",
            message="Timeout 30000ms exceeded",
            at_unix_nano=FAILED_AT,
            url=failed_flow.target,
            browser_version="142.0.7444.51",
        ),
    )
    record = _record_with_severity(_logs_of(_build(without)), logs_pb2.SEVERITY_NUMBER_ERROR)

    # The key the fixture carries and this record deliberately does not.
    frozen_keys = _attribute_keys(_record_with_severity(contract, logs_pb2.SEVERITY_NUMBER_ERROR))
    assert "synthetic.check.screenshot.ref" in frozen_keys
    assert _attribute_keys(record) == frozen_keys - {"synthetic.check.screenshot.ref"}


def test_a_flow_that_could_not_be_read_is_published_as_a_failure(
    contract: logs_service_pb2.ExportLogsServiceRequest,
) -> None:
    """A check nobody can run must be visible, not silently absent."""
    from nexovue_scheduler.probes import browser

    result = browser.perform(
        CHECK_ID,
        "https://shop.acme.example/checkout",
        timeout_seconds=10,
        config={"steps": [{"action": "goto", "url": "https://x.example"}, {"action": "teleport"}]},
    )

    assert result.failure is not None
    assert result.failure.error_type == ERROR_TYPE_CONFIGURATION
    assert result.failure.index == 1
    assert result.steps_total == 2
    assert result.steps_passed == 1

    record = _record_with_severity(_logs_of(_build(result)), logs_pb2.SEVERITY_NUMBER_ERROR)
    assert "teleport" in record.body.string_value
