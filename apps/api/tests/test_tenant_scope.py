"""The multi-tenancy invariant, exercised rather than asserted in a comment.

`TenantScopedRepository` is the only way this service reads telemetry, so these
tests are what stands between "isolation is enforced" and "isolation is a
convention everyone has remembered so far".
"""

from __future__ import annotations

from typing import Any

import pytest

from nexovue_api.data.clickhouse.base import (
    TENANT_BINDING,
    TenantFilterMissingError,
    TenantScopedRepository,
)

TENANT_A = "org-a"
TENANT_B = "org-b"

_SCOPED_SQL = f"SELECT trace_id FROM spans WHERE tenant_id = {TENANT_BINDING}"


class FakeQueryResult:
    def __init__(self, column_names: list[str], result_rows: list[tuple[Any, ...]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class FakeClickHouse:
    """A store that answers only with the rows belonging to the bound tenant.

    It reads `tenant_id` out of the parameters the repository bound, which is
    exactly what the real server does with `{tenant_id:String}`.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_parameters: dict[str, Any] | None = None

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> FakeQueryResult:
        self.last_parameters = dict(parameters or {})
        tenant = self.last_parameters.get("tenant_id")
        matching = [row for row in self._rows if row["tenant_id"] == tenant]
        return FakeQueryResult(["trace_id"], [(row["trace_id"],) for row in matching])


def _two_tenant_store() -> FakeClickHouse:
    return FakeClickHouse(
        [
            {"tenant_id": TENANT_A, "trace_id": "aaaa"},
            {"tenant_id": TENANT_B, "trace_id": "bbbb"},
        ]
    )


async def test_a_query_without_the_tenant_binding_raises() -> None:
    repository = TenantScopedRepository(FakeClickHouse([]), tenant_id=TENANT_A)  # type: ignore[arg-type]

    with pytest.raises(TenantFilterMissingError):
        await repository.query("SELECT trace_id FROM spans")


async def test_the_unscoped_query_never_reaches_the_driver() -> None:
    store = _two_tenant_store()
    repository = TenantScopedRepository(store, tenant_id=TENANT_A)  # type: ignore[arg-type]

    with pytest.raises(TenantFilterMissingError):
        await repository.query("SELECT trace_id FROM spans WHERE tenant_id = 'org-b'")

    assert store.last_parameters is None, "the driver was called despite the missing binding"


async def test_two_organizations_cannot_read_each_others_traces() -> None:
    store = _two_tenant_store()

    for_a = await TenantScopedRepository(store, tenant_id=TENANT_A).query(_SCOPED_SQL)  # type: ignore[arg-type]
    for_b = await TenantScopedRepository(store, tenant_id=TENANT_B).query(_SCOPED_SQL)  # type: ignore[arg-type]

    assert [row["trace_id"] for row in for_a] == ["aaaa"]
    assert [row["trace_id"] for row in for_b] == ["bbbb"]


async def test_a_caller_cannot_override_the_tenant_it_was_constructed_with() -> None:
    """The binding is applied after the caller's parameters, so it always wins."""
    store = _two_tenant_store()
    repository = TenantScopedRepository(store, tenant_id=TENANT_A)  # type: ignore[arg-type]

    rows = await repository.query(_SCOPED_SQL, {"tenant_id": TENANT_B})

    assert [row["trace_id"] for row in rows] == ["aaaa"]
    assert store.last_parameters is not None
    assert store.last_parameters["tenant_id"] == TENANT_A
