"""What detection's store refuses before anything reaches ClickHouse (ADR-023, plan T2b.2)."""

from __future__ import annotations

from datetime import datetime
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


def test_a_reading_without_a_time_zone_is_refused() -> None:
    naive = Reading(
        service_name="checkout",
        minute=datetime(2026, 11, 1, 12, 0),  # noqa: DTZ001  the refusal under test
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

    with pytest.raises(ValueError, match="timezone-aware"):
        _detection().write([naive])


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
