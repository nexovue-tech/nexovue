"""The tenancy chokepoint.

Every ClickHouse query in this service is built here, so the tenant filter is
bound in one place rather than remembered in twelve.

**What guarantees what.** Isolation rests on two layers, and it is worth being
exact about which one is load-bearing, because an earlier version of this file
claimed more than it delivered.

The *guarantee* is structural, in `tests/test_tenant_chokepoint_structure.py`: it
parses every module under `src/` and fails if any statement reads a telemetry
table without the tenant predicate, if any such statement is assembled from a
runtime value, or if the raw driver is reached outside this package. Every query
in this service is a module constant, so that check is total.

This runtime check is the *backstop*. It cannot be a complete guarantee, because
deciding what arbitrary SQL does by inspecting its text is not soundly possible —
the predicate has to be a real conjunct, and only a parser knows that. What it
does do is reject the mistake anyone actually makes: forgetting the filter. It
now also rejects the three near-misses that used to pass, all of which return
every tenant's rows while containing the required text:

    /* {tenant_id:String} */ SELECT ... FROM spans     -- inside a comment
    ... WHERE '{tenant_id:String}' != ''               -- inside a string literal
    SELECT {tenant_id:String}, ... FROM spans          -- projected, not filtered

**Reads only.** The API connects as a user that can only read. Erasing a
deleted tenant's telemetry is the scheduler's, at night (ADR-014).
"""

from typing import Any, Final

from clickhouse_connect.driver.asyncclient import AsyncClient
from nexovue_platform import tenant_statements

# The binding, the predicate check and the read limits are shared with the
# scheduler's reader (F21, ADR-012), so there is one definition of "this statement
# is scoped to a tenant". Re-exported here for the modules that already import them.
TENANT_BINDING: Final = tenant_statements.TENANT_BINDING
TENANT_PREDICATE: Final = tenant_statements.TENANT_PREDICATE
MAX_EXECUTION_SECONDS: Final = tenant_statements.MAX_EXECUTION_SECONDS
MAX_RESULT_ROWS: Final = tenant_statements.MAX_RESULT_ROWS
READ_SETTINGS: Final = tenant_statements.READ_SETTINGS
TenantFilterMissingError = tenant_statements.TenantFilterMissingError
binds_tenant = tenant_statements.binds_tenant


class TenantScopedRepository:
    """Reads telemetry for exactly one tenant.

    Construct it per request from the authenticated session; never share one
    across tenants.
    """

    def __init__(self, client: AsyncClient, tenant_id: str, project_id: str | None = None) -> None:
        self._client = client
        self._tenant_id = tenant_id
        self._project_id = project_id

    async def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a read, scoped to this tenant, bounded in time and rows."""
        bound: dict[str, Any] = {**(parameters or {}), "tenant_id": self._tenant_id}
        if self._project_id is not None:
            bound["project_id"] = self._project_id
        return await _tenant_scoped_read(self._client, sql, bound)

    async def query_one(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """The single-row form. Returns None rather than raising on no match."""
        rows = await self.query(sql, parameters)
        return rows[0] if rows else None


async def _tenant_scoped_read(
    client: AsyncClient, sql: str, bound: dict[str, Any]
) -> list[dict[str, Any]]:
    """One read through the binding."""
    if not binds_tenant(sql):
        raise TenantFilterMissingError(sql)

    result = await client.query(sql, parameters=bound, settings=READ_SETTINGS)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
