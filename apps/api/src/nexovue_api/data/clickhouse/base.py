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
"""

import re
from typing import Any, Final

from clickhouse_connect.driver.asyncclient import AsyncClient

# ClickHouse's server-side parameter syntax.
TENANT_BINDING: Final = "{tenant_id:String}"

# The predicate itself, not merely a mention of the parameter. Whitespace varies;
# nothing else may.
TENANT_PREDICATE: Final = re.compile(r"\btenant_id\s*=\s*\{tenant_id:String\}")

# Comments and single-quoted literals are stripped before the predicate is looked
# for, because ClickHouse does not substitute a parameter in either — text there
# reads as the requirement while doing nothing.
_COMMENTS: Final = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_STRING_LITERALS: Final = re.compile(r"'(?:[^'\\]|\\.|'')*'")

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
            f"query has no `tenant_id = {TENANT_BINDING}` predicate outside of "
            f"comments and string literals, and would read across tenants: "
            f"{sql.strip()[:200]}"
        )


def _executable_text(sql: str) -> str:
    """The SQL with everything ClickHouse will not act on removed."""
    return _STRING_LITERALS.sub("''", _COMMENTS.sub(" ", sql))


def binds_tenant(sql: str) -> bool:
    """Whether the statement filters on the tenant, as opposed to mentioning it."""
    return bool(TENANT_PREDICATE.search(_executable_text(sql)))


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
        if not binds_tenant(sql):
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
