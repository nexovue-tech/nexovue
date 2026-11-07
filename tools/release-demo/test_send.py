"""Each mode's shape, the export it builds, and the ledger it keeps, without a receiver."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from send import (
    COLD_FACTOR,
    NEW_EXCEPTION,
    NEW_VERSION,
    OLD_VERSION,
    TICKS_PER_MINUTE,
    Batch,
    Ledger,
    Settings,
    compare,
    export_request,
    load_ledger,
    run,
    save_ledger,
    service_names,
    slices_for,
    unsummed_services,
)

AT = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


def _shares(settings: Settings, minute: int, service: int = 0) -> dict[str | None, float]:
    return {piece.version: piece.share for piece in slices_for(settings, service, minute)}


def test_every_minute_of_every_mode_shares_out_all_the_traffic() -> None:
    for mode in ("rolling", "swap", "no-version", "rollback", "exceptions", "cold-start", "churn"):
        settings = Settings(mode=mode)
        for minute in range(60):
            assert sum(_shares(settings, minute).values()) == pytest.approx(1.0), (mode, minute)
    hourly = Settings(mode="many-services", services=7)
    for service in range(7):
        for minute in range(130):
            assert sum(_shares(hourly, minute, service).values()) == pytest.approx(1.0)


def test_rolling_hands_traffic_over_evenly_and_both_serve_the_same_minutes() -> None:
    settings = Settings(mode="rolling", start_minute=5, rollout_minutes=4)
    assert _shares(settings, 4) == {OLD_VERSION: 1.0}
    rising = [_shares(settings, minute)[NEW_VERSION] for minute in range(5, 9)]
    assert rising == sorted(rising) and 0 < rising[0] < rising[-1] < 1
    assert set(_shares(settings, 6)) == {OLD_VERSION, NEW_VERSION}
    assert _shares(settings, 9) == {NEW_VERSION: 1.0}
    failure = {piece.version: piece.failure_rate for piece in slices_for(settings, 0, 6)}
    assert failure == {OLD_VERSION: settings.old_failure, NEW_VERSION: settings.new_failure}


def test_swap_and_rollback_change_version_in_one_minute() -> None:
    swap = Settings(mode="swap", start_minute=5)
    assert (_shares(swap, 4), _shares(swap, 5)) == ({OLD_VERSION: 1.0}, {NEW_VERSION: 1.0})
    back = Settings(mode="rollback", start_minute=5, rollout_minutes=10)
    assert [_shares(back, m) for m in (4, 5, 14, 15)] == [
        {OLD_VERSION: 1.0},
        {NEW_VERSION: 1.0},
        {NEW_VERSION: 1.0},
        {OLD_VERSION: 1.0},
    ]


def test_no_version_sends_none_and_steps_its_failure_rate() -> None:
    settings = Settings(mode="no-version", start_minute=5)
    assert slices_for(settings, 0, 4)[0].version is None
    assert slices_for(settings, 0, 4)[0].failure_rate == settings.old_failure
    assert slices_for(settings, 0, 5)[0].failure_rate == settings.new_failure


def test_cold_start_is_slow_only_for_its_first_minutes() -> None:
    settings = Settings(mode="cold-start", start_minute=5)
    assert slices_for(settings, 0, 5)[0].latency_factor == COLD_FACTOR
    assert slices_for(settings, 0, 10)[0].latency_factor == 1.0


def test_churn_names_fresh_versions_every_minute() -> None:
    settings = Settings(mode="churn", versions_per_minute=30)
    first, second = set(_shares(settings, 0)), set(_shares(settings, 1))
    assert len(first) == 30 and not first & second


def test_many_services_roll_out_once_an_hour_each_staggered() -> None:
    settings = Settings(mode="many-services", services=4, rollout_minutes=5)
    assert service_names(settings) == ["svc-000", "svc-001", "svc-002", "svc-003"]
    for service in range(4):
        rolling = [m for m in range(120) if len(_shares(settings, m, service)) == 2]
        # Five minutes in each of the two hours.
        assert len(rolling) == 10
    starts = {min(m for m in range(60) if len(_shares(settings, m, s)) == 2) for s in range(4)}
    assert len(starts) == 4


def test_the_export_carries_the_version_on_the_resource_or_none_at_all() -> None:
    body = export_request(
        [
            Batch("checkout", "v2", requests=3, failures=1, latency_factor=1.0, throws=True),
            Batch("checkout", None, requests=2, failures=0, latency_factor=1.0, throws=False),
        ],
        AT,
        random.Random(1),
    )
    request = ExportTraceServiceRequest.FromString(body)

    versioned, unversioned = request.resource_spans
    attributes = {a.key: a.value.string_value for a in versioned.resource.attributes}
    assert attributes == {"service.name": "checkout", "service.version": "v2"}
    assert [a.key for a in unversioned.resource.attributes] == ["service.name"]
    spans = versioned.scope_spans[0].spans
    assert len(spans) == 3 and all(s.kind == s.SPAN_KIND_SERVER for s in spans)
    assert all(s.start_time_unix_nano == int(AT.timestamp() * 1e9) for s in spans)
    thrown = [e for s in spans for e in s.events if e.name == "exception"]
    assert len(thrown) == 1 and thrown[0].attributes[0].value.string_value == NEW_EXCEPTION


def test_the_ledger_records_only_what_the_receiver_accepted() -> None:
    statuses = iter([200, 503, 200] * 100)
    sent: list[bytes] = []

    def post(body: bytes) -> int:
        sent.append(body)
        return next(statuses)

    ledger = run(
        Settings(mode="rolling", rpm=600, start_minute=0, rollout_minutes=1),
        minutes=1,
        post=post,
        seed=3,
        clock=lambda: 0.0,
        sleep=lambda _: None,
        now=lambda: AT,
    )

    accepted = [
        ExportTraceServiceRequest.FromString(body)
        for index, body in enumerate(sent)
        if index % 3 != 1
    ]
    spans = sum(
        len(rs.scope_spans[0].spans) for request in accepted for rs in request.resource_spans
    )
    assert len(sent) == TICKS_PER_MINUTE
    assert ledger.refused_exports == TICKS_PER_MINUTE // 3
    assert ledger.total_requests() == spans > 0


def test_compare_names_every_difference_both_ways() -> None:
    ledger = Ledger("rolling")
    ledger.record(
        [Batch("checkout", "v1", 10, 1, 1.0, False), Batch("checkout", None, 4, 0, 1.0, False)]
    )
    stored = {("checkout", "v1"): (10, 1), ("checkout", ""): (4, 0)}
    assert compare(ledger.sent, stored) == []

    short = stored | {("checkout", "v1"): (9, 1), ("checkout", "v9"): (1, 0)}
    found = {(m.service, m.version): (m.sent, m.stored) for m in compare(ledger.sent, short)}
    assert found == {("checkout", "v1"): ((10, 1), (9, 1)), ("checkout", "v9"): ((0, 0), (1, 0))}


def test_versions_that_do_not_sum_to_the_service_row_are_named() -> None:
    versions = {("a", "v1"): (5, 1), ("a", "v2"): (5, 0), ("b", ""): (3, 0)}
    assert unsummed_services(versions, {"a": (10, 1), "b": (3, 0)}) == []
    assert unsummed_services(versions, {"a": (11, 1), "b": (3, 0), "c": (1, 0)}) == ["a", "c"]


def test_a_ledger_written_by_send_reads_back_as_it_was(tmp_path: Path) -> None:
    ledger = Ledger("swap", started_at=AT.isoformat(), ended_at=AT.isoformat(), refused_exports=2)
    ledger.record([Batch("checkout", "v2", 7, 1, 1.0, False)])
    path = tmp_path / "ledger.json"

    save_ledger(ledger, path)

    assert load_ledger(str(path)) == ledger
