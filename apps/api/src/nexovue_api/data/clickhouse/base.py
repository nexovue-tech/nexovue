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

**Reads and the one writer.** `TenantScopedRepository` is the read path. Deleting
a tenant's telemetry is the one operation whose whole job is to name a tenant, so
it binds through the same predicate — `TenantScopedPurger` — but is a separate
class, because it is built from an operator's deletion request rather than from a
browsing session and no reader should hold a DELETE verb.
"""

import re
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

# `ALTER TABLE ... DELETE` is a mutation: ClickHouse rewrites every affected
# part, which takes as long as it takes. 0 is "record the obligation and
# return" — the server has it durably and resumes it across a restart, so the
# caller observes completion rather than waiting on the statement.
MUTATIONS_ASYNCHRONOUS: Final = 0

# The only unscoped reads allowed: ClickHouse's own bookkeeping, which has no
# tenant column to filter on.
_READS_A_SYSTEM_TABLE: Final = re.compile(r"\bFROM\s+system\.", re.IGNORECASE)


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
    """One read through the binding, for the repository and the purger alike."""
    if not binds_tenant(sql):
        raise TenantFilterMissingError(sql)

    result = await client.query(sql, parameters=bound, settings=READ_SETTINGS)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


class SystemTableOnlyError(RuntimeError):
    """An unscoped read named something other than a `system.` table.

    The unscoped door exists for ClickHouse's own bookkeeping, which carries no
    tenant column. Anything else reaching it would read every tenant's rows.
    """

    def __init__(self, sql: str) -> None:
        super().__init__(f"unscoped reads may only name a system table: {sql.strip()[:200]}")


class TenantScopedPurger:
    """Removes one tenant's telemetry, and reports what is left.

    Constructed from a deletion request, never from a session. It binds the
    tenant through the same predicate the read path does, so a purge statement
    that forgot the filter is refused here rather than deleting the store.
    """

    def __init__(self, client: AsyncClient, tenant_id: str) -> None:
        self._client = client
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        """Whose rows this purger removes. Read by the caller that reports on it."""
        return self._tenant_id

    @property
    def database(self) -> str:
        """The ClickHouse database in play — `system.mutations` spans all of them."""
        return str(self._client.database)

    async def delete(self, statement: str) -> None:
        """Submit one `ALTER TABLE ... DELETE`. Returns once the server holds it."""
        if not binds_tenant(statement):
            raise TenantFilterMissingError(statement)

        await self._client.command(
            statement,
            parameters={"tenant_id": self._tenant_id},
            settings={"mutations_sync": MUTATIONS_ASYNCHRONOUS},
        )

    async def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """A read scoped to the tenant being purged — how many rows are left."""
        bound: dict[str, Any] = {**(parameters or {}), "tenant_id": self._tenant_id}
        return await _tenant_scoped_read(self._client, sql, bound)

    async def query_server_metadata(
        self, sql: str, parameters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A read of ClickHouse's own bookkeeping — mutation progress and failures.

        No tenant predicate is possible: `system.mutations` has no tenant column,
        and the caller narrows it by bound value instead. Restricted to `system.`
        so this cannot become a second way to read telemetry.
        """
        if not _READS_A_SYSTEM_TABLE.search(tenant_statements.executable_text(sql)):
            raise SystemTableOnlyError(sql)

        result = await self._client.query(sql, parameters=parameters, settings=READ_SETTINGS)
        return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
