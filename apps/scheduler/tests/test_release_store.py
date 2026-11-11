"""What the release sync's store reads, and under which bounds (F28 plan T1.11).

The statement is held to both bindings by `test_tenant_chokepoint_structure.py`, which
walks this package as project-bound; here, with `tenant_statements`, the statement binds
both, the reader fills them from the project it was handed out for, and the read is
bounded below the shared one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexovue_platform.tenant_statements import READ_SETTINGS, binds_project, binds_tenant

from nexovue_scheduler.alerts.telemetry import TelemetryStore
from nexovue_scheduler.releases import store
from nexovue_scheduler.releases.store import (
    RELEASE_READ_SETTINGS,
    RELEASE_VERSION_MINUTES_SQL,
    ReleaseStore,
    VersionCount,
)

START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


class _Result:
    column_names = ("service_name", "minute_epoch", "version", "requests")
    result_rows = (("checkout", int(START.timestamp()), "v2", 12),)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def query(self, sql: str, *, parameters: dict[str, Any], settings: dict[str, Any]) -> _Result:
        self.calls.append((sql, parameters, settings))
        return _Result()


def test_the_statement_binds_the_tenant_and_the_project() -> None:
    assert binds_tenant(RELEASE_VERSION_MINUTES_SQL)
    assert binds_project(RELEASE_VERSION_MINUTES_SQL)


def test_no_version_and_combined_versions_are_never_read() -> None:
    """FR17, FR18: neither can become a release."""
    assert "version NOT IN ('', '__other__')" in RELEASE_VERSION_MINUTES_SQL


def test_the_reader_binds_the_project_it_was_handed_out_for() -> None:
    client = _RecordingClient()
    reader = ReleaseStore(TelemetryStore(client)).for_project("org-a", "project-a")  # type: ignore[arg-type]

    rows = reader.version_minutes(start=START, end=START + timedelta(hours=1))

    assert rows == [VersionCount("checkout", START, "v2", 12)]
    ((sql, parameters, settings),) = client.calls
    assert sql == RELEASE_VERSION_MINUTES_SQL
    assert (parameters["tenant_id"], parameters["project_id"]) == ("org-a", "project-a")
    assert settings == dict(RELEASE_READ_SETTINGS)


def test_reads_are_bounded_below_the_shared_reads() -> None:
    """AC72: a slow ClickHouse holds one of two seats for 10 seconds at most."""
    for bound in ("max_execution_time", "max_memory_usage", "max_bytes_before_external_group_by"):
        assert RELEASE_READ_SETTINGS[bound] < READ_SETTINGS[bound], bound
    assert RELEASE_READ_SETTINGS["max_execution_time"] == 10


def test_the_store_holds_no_driver_of_its_own() -> None:
    """The reader is `alerts.telemetry`'s, the scheduler's one module with the driver."""
    assert not hasattr(store, "clickhouse_connect")
