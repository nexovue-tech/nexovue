"""A dashboard scan's repository, bounded in time and cancelled on disconnect (F20, T2.9).

A read abandoned at the batch's deadline would otherwise run on to the shared 30 s
limit while the next refresh starts more (design section 6). `bounded_by` gives a
repository for the same tenant and project whose reads send a lowered time limit and
`cancel_http_readonly_queries_on_client_close`; nothing else sends that setting, and
the shared bounds the scheduler's alert and detection reads derive from do not change
(AC93).
"""

from __future__ import annotations

from typing import Any, Final, cast

import pytest
from clickhouse_connect.driver.asyncclient import AsyncClient
from nexovue_platform import tenant_statements
from nexovue_platform.tenant_statements import LimitRaisedError

from nexovue_api.data.clickhouse.base import CANCEL_ON_CLIENT_CLOSE, TenantScopedRepository
from nexovue_scheduler.detection.store import DETECTION_READ_SETTINGS

STATEMENT: Final = "SELECT 1 FROM spans WHERE tenant_id = {tenant_id:String}"

# What the shared bounds were when F20 began (`tenant_statements.py`), restated so a change
# made for dashboards is a failed test and not a quiet change to every alert read.
SHARED_READ_SETTINGS: Final = {
    "max_execution_time": 30,
    "max_result_rows": 500_000,
    "result_overflow_mode": "throw",
    "max_memory_usage": 1 << 30,
    "max_bytes_before_external_group_by": 1 << 29,
}
DETECTION_SETTINGS: Final = SHARED_READ_SETTINGS | {
    "max_execution_time": 5,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_before_external_group_by": 128 * 1024 * 1024,
}


class _Result:
    def __init__(self) -> None:
        self.column_names = ["one"]
        self.result_rows = [(1,)]


class _Client:
    """Records the settings and parameters each read sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def query(
        self, sql: str, *, parameters: dict[str, Any], settings: dict[str, Any]
    ) -> _Result:
        self.sent.append((dict(settings), dict(parameters)))
        return _Result()


def _repository(client: _Client, project_id: str | None = "project-1") -> TenantScopedRepository:
    return TenantScopedRepository(cast(AsyncClient, client), "org-a", project_id)


async def test_a_bounded_repository_sends_its_limit_and_the_cancel_setting() -> None:
    client = _Client()

    await _repository(client).bounded_by(15).query(STATEMENT)

    [(settings, parameters)] = client.sent
    assert settings["max_execution_time"] == 15
    assert settings[CANCEL_ON_CLIENT_CLOSE] == 1
    assert {key: settings[key] for key in SHARED_READ_SETTINGS if key != "max_execution_time"} == {
        key: value for key, value in SHARED_READ_SETTINGS.items() if key != "max_execution_time"
    }
    assert parameters == {"tenant_id": "org-a", "project_id": "project-1"}


async def test_the_repository_it_came_from_sends_neither() -> None:
    client = _Client()
    repository = _repository(client)
    repository.bounded_by(15)

    await repository.query(STATEMENT)

    [(settings, _)] = client.sent
    assert settings == SHARED_READ_SETTINGS
    assert CANCEL_ON_CLIENT_CLOSE not in settings


async def test_a_bounded_repository_reads_for_the_same_tenant_only() -> None:
    client = _Client()

    with pytest.raises(tenant_statements.TenantFilterMissingError):
        await _repository(client).bounded_by(15).query("SELECT 1 FROM spans")
    assert client.sent == []


@pytest.mark.parametrize("seconds", [0, 31, -1])
def test_a_bound_can_only_be_lowered(seconds: int) -> None:
    """ClickHouse reads 0 as unlimited; above the shared 30 s would loosen the bound."""
    with pytest.raises(LimitRaisedError):
        _repository(_Client()).bounded_by(seconds)


def test_ac93_the_shared_and_detection_bounds_are_unchanged() -> None:
    assert tenant_statements.READ_SETTINGS == SHARED_READ_SETTINGS
    assert DETECTION_READ_SETTINGS == DETECTION_SETTINGS
    assert CANCEL_ON_CLIENT_CLOSE not in tenant_statements.READ_SETTINGS
    assert CANCEL_ON_CLIENT_CLOSE not in DETECTION_READ_SETTINGS
