"""Round-trip proof for the frozen envelope, asserted from Python.

The Go suite in ../roundtrip_test.go states the same expectations independently.
Two readers agreeing is what makes "frozen contract" a fact rather than an assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from google.protobuf.message import Message
from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

FIXTURES_DIR: Final = Path(__file__).resolve().parent.parent / "fixtures"

TENANT_ID: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
SEMCONV_VERSION: Final = "1.44.0"

NANOS_PER_MILLISECOND: Final = 1_000_000
BASE_UNIX_NANO: Final = 1787649300 * 1_000_000_000

# One row per door, exactly the list in docs/architecture/ingestion-envelope.md:
# filename, door, signal, ingest_id, offset from BASE_UNIX_NANO in milliseconds.
EXPECTATIONS: Final = [
    (
        "door1-traces.bin",
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_SPANS,
        "01996d5e-9a40-7c01-a000-000000000001",
        430,
    ),
    (
        "door1-logs.bin",
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_LOGS,
        "01996d5e-9a40-7c01-a000-000000000002",
        1_260,
    ),
    (
        "door1-metrics.bin",
        envelope_pb2.DOOR_OTLP,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000003",
        60_080,
    ),
    (
        "door2-synthetic-http.bin",
        envelope_pb2.DOOR_SYNTHETIC,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000004",
        310,
    ),
    (
        "door2-synthetic-browser.bin",
        envelope_pb2.DOOR_SYNTHETIC,
        envelope_pb2.SIGNAL_LOGS,
        "01996d5e-9a40-7c01-a000-000000000005",
        32_190,
    ),
    (
        "door3-rum.bin",
        envelope_pb2.DOOR_RUM,
        envelope_pb2.SIGNAL_SPANS,
        "01996d5e-9a40-7c01-a000-000000000006",
        2_050,
    ),
    (
        "door4b-cloudwatch.bin",
        envelope_pb2.DOOR_POLLER,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000007",
        60_140,
    ),
    (
        "door4b-dbstats.bin",
        envelope_pb2.DOOR_POLLER,
        envelope_pb2.SIGNAL_METRICS,
        "01996d5e-9a40-7c01-a000-000000000008",
        60_210,
    ),
]

FIXTURE_IDS: Final = [expectation[0] for expectation in EXPECTATIONS]


def otlp_request_for(signal: envelope_pb2.Signal) -> Message:
    """The OTLP request type the signal names — the only thing a payload may be."""
    if signal == envelope_pb2.SIGNAL_SPANS:
        return trace_service_pb2.ExportTraceServiceRequest()
    if signal == envelope_pb2.SIGNAL_LOGS:
        return logs_service_pb2.ExportLogsServiceRequest()
    if signal == envelope_pb2.SIGNAL_METRICS:
        return metrics_service_pb2.ExportMetricsServiceRequest()
    raise AssertionError(f"signal {signal} routes to no OTLP request type")


def resource_entries(request: Message) -> list[object]:
    for field in ("resource_spans", "resource_logs", "resource_metrics"):
        if request.DESCRIPTOR.fields_by_name.get(field) is not None:
            return list(getattr(request, field))
    raise AssertionError("OTLP request carries no resource_* field")


def read_fixture(filename: str) -> bytes:
    return (FIXTURES_DIR / filename).read_bytes()


def test_every_door_has_a_fixture() -> None:
    on_disk = sorted(path.name for path in FIXTURES_DIR.glob("*.bin"))
    assert on_disk == sorted(FIXTURE_IDS)


@pytest.mark.parametrize(
    ("filename", "door", "signal", "ingest_id", "received_offset_millis"),
    EXPECTATIONS,
    ids=FIXTURE_IDS,
)
def test_fixture_decodes_to_expected_fields(
    filename: str,
    door: envelope_pb2.Door,
    signal: envelope_pb2.Signal,
    ingest_id: str,
    received_offset_millis: int,
) -> None:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(read_fixture(filename))

    assert envelope.tenant_id == TENANT_ID
    assert envelope.project_id == PROJECT_ID
    assert envelope.door == door
    assert envelope.signal == signal
    assert envelope.ingest_id == ingest_id
    assert envelope.semconv_version == SEMCONV_VERSION
    assert envelope.received_at_unix_nano == (
        BASE_UNIX_NANO + received_offset_millis * NANOS_PER_MILLISECOND
    )
    assert envelope.payload, "payload is empty — the fixture is a stub, not a recorded payload"


@pytest.mark.parametrize("filename", FIXTURE_IDS)
def test_fixture_re_encodes_byte_identically(filename: str) -> None:
    """The point of the whole contract: bytes in, identical bytes out."""
    encoded = read_fixture(filename)

    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(encoded)

    assert envelope.SerializeToString(deterministic=True) == encoded


@pytest.mark.parametrize("filename", FIXTURE_IDS)
def test_payload_parses_as_its_signals_otlp_request(filename: str) -> None:
    """A payload the envelope carried but corrupted passes the byte check and fails here."""
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(read_fixture(filename))

    request = otlp_request_for(envelope.signal)
    request.ParseFromString(envelope.payload)

    assert resource_entries(request), "OTLP payload carries no resource entries"
    assert request.SerializeToString(deterministic=True) == envelope.payload
