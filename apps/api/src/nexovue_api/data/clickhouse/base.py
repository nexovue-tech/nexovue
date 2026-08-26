"""The tenancy chokepoint.

Every ClickHouse query in this service is built here, so the tenant filter is
injected in one place rather than remembered in twelve. The whole
multi-tenancy invariant rests on this file: if a query can reach the driver
without a tenant binding, isolation is a convention rather than a guarantee.
"""

from typing import Any, Final

from clickhouse_connect.driver.asyncclient import AsyncClient

# ClickHouse's server-side parameter syntax. A query that does not reference
# this cannot have been scoped to a tenant.
TENANT_BINDING: Final = "{tenant_id:String}"

# A single request must never be able to exhaust the server. These are the
# last line of defence; the client is configured with them too, so a caller
# would have to defeat both to run unbounded.
MAX_EXECUTION_SECONDS: Final = 30
MAX_RESULT_ROWS: Final = 500_000


class TenantFilterMissingError(RuntimeError):
    """A query reached the repository without binding a tenant.

    Raised rather than silently scoped, because a query written without the
    binding was written by someone who was not thinking about tenancy, and
    the next one they write will be wrong too.
    """

    def __init__(self, sql: str) -> None:
        super().__init__(
            f"query does not reference {TENANT_BINDING} and would read across "
            f"tenants: {sql.strip()[:200]}"
        )


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
        if TENANT_BINDING not in sql:
            raise TenantFilterMissingError(sql)

        bound: dict[str, Any] = {**(parameters or {}), "tenant_id": self._tenant_id}
        if self._project_id is not None:
            bound["project_id"] = self._project_id

        result = await self._client.query(
            sql,
            parameters=bound,
            settings={
                "max_execution_time": MAX_EXECUTION_SECONDS,
                "max_result_rows": MAX_RESULT_ROWS,
                "result_overflow_mode": "throw",
            },
        )
        return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]

    async def query_one(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """The single-row form. Returns None rather than raising on no match."""
        rows = await self.query(sql, parameters)
        return rows[0] if rows else None
