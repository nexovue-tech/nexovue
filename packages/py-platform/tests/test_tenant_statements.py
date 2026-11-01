"""The shared statement checks and read bounds both telemetry readers use (ADR-012, ADR-023)."""

from __future__ import annotations

import pytest
from nexovue_platform.tenant_statements import (
    MAX_EXECUTION_SECONDS,
    MAX_MEMORY_BYTES,
    READ_SETTINGS,
    LimitRaisedError,
    binds_project,
    lowered_read_settings,
)


def test_a_lowered_bound_replaces_the_shared_one_and_leaves_the_rest() -> None:
    settings = lowered_read_settings(max_execution_time=5)

    assert settings["max_execution_time"] == 5
    assert settings["max_memory_usage"] == MAX_MEMORY_BYTES
    assert settings["result_overflow_mode"] == READ_SETTINGS["result_overflow_mode"]
    assert READ_SETTINGS["max_execution_time"] == MAX_EXECUTION_SECONDS, "the shared bounds moved"


@pytest.mark.parametrize(
    "override",
    [
        {"max_execution_time": MAX_EXECUTION_SECONDS + 1},
        {"max_memory_usage": MAX_MEMORY_BYTES * 2},
        # ClickHouse reads 0 as "no limit", the highest raise there is.
        {"max_result_rows": 0},
        {"max_memory_usage": -1},
    ],
)
def test_an_override_that_raises_a_limit_is_refused(override: dict[str, int]) -> None:
    with pytest.raises(LimitRaisedError):
        lowered_read_settings(**override)


@pytest.mark.parametrize("name", ["result_overflow_mode", "readonly", "max_threads"])
def test_an_override_of_something_that_is_not_a_bound_is_refused(name: str) -> None:
    with pytest.raises(LimitRaisedError):
        lowered_read_settings(**{name: 1})


@pytest.mark.parametrize(
    ("sql", "bound"),
    [
        ("SELECT 1 FROM t WHERE project_id = {project_id:String}", True),
        ("SELECT 1 FROM t WHERE project_id={project_id:String}", True),
        ("SELECT 1 FROM t /* project_id = {project_id:String} */", False),
        ("SELECT 1 FROM t WHERE x = 'project_id = {project_id:String}'", False),
        ("SELECT {project_id:String} FROM t", False),
        ("SELECT 1 FROM t WHERE tenant_id = {tenant_id:String}", False),
    ],
)
def test_a_project_is_bound_only_by_a_real_predicate(sql: str, bound: bool) -> None:
    assert binds_project(sql) is bound
