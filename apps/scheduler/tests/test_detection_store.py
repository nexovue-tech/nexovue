"""What detection's store refuses before anything reaches ClickHouse (ADR-023, plan T2b.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Never

import pytest
from nexovue_platform.tenant_statements import (
    READ_SETTINGS,
    ProjectFilterMissingError,
    TenantFilterMissingError,
)

from nexovue_scheduler.detection import store
from nexovue_scheduler.detection.store import (
    DETECTION_READ_SETTINGS,
    MinuteCounts,
    ProjectBinding,
    ProjectDetection,
    Reading,
)

BINDING = ProjectBinding(org_id="org-a", project_id="project-a", derived_retention_days=30)


class _ClientThatMustNotBeReached:
    def query(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError("a refused read reached the store")

    def insert(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError("a refused write reached the store")


def _detection() -> ProjectDetection:
    client: Any = _ClientThatMustNotBeReached()
    return ProjectDetection(client, client, BINDING)


def test_detection_reads_are_bounded_below_the_shared_reads() -> None:
    for bound in ("max_execution_time", "max_memory_usage", "max_bytes_before_external_group_by"):
        assert DETECTION_READ_SETTINGS[bound] < READ_SETTINGS[bound], bound
    assert DETECTION_READ_SETTINGS["max_execution_time"] == 5
    assert DETECTION_READ_SETTINGS["max_memory_usage"] == 256 * 1024 * 1024


@pytest.mark.parametrize(
    ("sql", "refusal"),
    [
        (
            "SELECT 1 FROM detection_readings WHERE project_id = {project_id:String}",
            TenantFilterMissingError,
        ),
        (
            "SELECT 1 FROM detection_readings WHERE tenant_id = {tenant_id:String}",
            ProjectFilterMissingError,
        ),
        (
            "SELECT 1 FROM detection_readings WHERE tenant_id = {tenant_id:String} "
            "/* AND project_id = {project_id:String} */",
            ProjectFilterMissingError,
        ),
    ],
)
def test_a_read_missing_either_binding_is_refused(sql: str, refusal: type[Exception]) -> None:
    with pytest.raises(refusal):
        _detection()._query(sql, {})


class _RecordingWriter:
    def __init__(self) -> None:
        self.settings: list[dict[str, Any] | None] = []

    def insert(self, *args: Any, settings: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.settings.append(settings)


def _reading(minute: datetime) -> Reading:
    return Reading(
        service_name="checkout",
        minute=minute,
        window_minutes=10,
        window_requests=0,
        window_errors=0,
        window_p95_ms=None,
        window_above_threshold=0,
        endpoint_failures={},
        traffic_mode="learning",
        traffic_days=0,
        traffic_median=None,
        traffic_spread=None,
        traffic_verdict="learning",
        failing_mode="learning",
        failing_days=0,
        failing_median=None,
        failing_spread=None,
        failing_burst_height=None,
        failing_burst_minutes=0,
        failing_verdict="learning",
        slower_mode="learning",
        slower_days=0,
        slower_median=None,
        slower_spread=None,
        slower_threshold_ms=None,
        slower_verdict="learning",
    )


def test_a_reading_without_a_time_zone_is_refused() -> None:
    naive = _reading(datetime(2026, 11, 1, 12, 0))  # noqa: DTZ001  the refusal under test

    with pytest.raises(ValueError, match="timezone-aware"):
        _detection().write([naive])


def test_readings_are_an_async_insert_the_job_waits_for() -> None:
    """Batched by the server rather than a part per project job, and written before the job
    goes on to its findings (design, *Performance and scale*). Stated, not left to the
    server's default, which changed in 26.2."""
    writer = _RecordingWriter()
    reader: Any = _ClientThatMustNotBeReached()
    detection = ProjectDetection(reader, writer, BINDING)  # type: ignore[arg-type]

    detection.write([_reading(datetime(2026, 11, 1, 12, 0, tzinfo=UTC))])

    assert writer.settings == [{"async_insert": 1, "wait_for_async_insert": 1}]


def test_minute_counts_hold_only_the_minutes_counted() -> None:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    counts = MinuteCounts(start, start + timedelta(minutes=10))
    counts.add(start + timedelta(minutes=3), 120, 4)

    assert counts.get(start + timedelta(minutes=3)) == (120, 4)
    assert counts.get(start + timedelta(minutes=4), (0, 0)) == (0, 0)
    assert counts.get(start - timedelta(minutes=1)) is None
    assert dict(counts) == {start + timedelta(minutes=3): (120, 4)}
    with pytest.raises(ValueError, match="outside"):
        counts.add(start + timedelta(minutes=10), 1, 0)


def test_writing_nothing_does_not_reach_the_store() -> None:
    _detection().write([])


@pytest.mark.parametrize(
    ("reader", "writer"), [("", "clickhouse://w@h/db"), ("clickhouse://r@h/db", "")]
)
def test_without_both_accounts_detection_is_off_and_says_so(
    monkeypatch: pytest.MonkeyPatch, reader: str, writer: str
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(store.logger, "error", lambda event, **_: errors.append(event))

    assert store.open_store(reader, writer) is None
    assert errors and "CLICKHOUSE_DETECTION_WRITER_DSN" in errors[0]
