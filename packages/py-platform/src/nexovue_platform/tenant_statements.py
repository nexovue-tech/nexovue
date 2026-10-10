"""Whether a telemetry statement binds the tenant — the one runtime check both
readers make, the API's and the scheduler's (F21, ADR-012).

Pure text inspection, which is why it can be shared by a package that reaches no
store. It is the *backstop*: the guarantee is structural
(`apps/api/tests/test_tenant_chokepoint_structure.py`, which walks both services'
source). What it rejects is the mistake anyone actually makes — forgetting the
filter — and the three near-misses that contain the required text and filter
nothing: inside a comment, inside a string literal, or projected rather than
compared.
"""

from __future__ import annotations

import re
from typing import Any, Final

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

# A single request must never be able to exhaust the server. The last line of
# defence; a caller would have to defeat both this and the client's own settings.
MAX_EXECUTION_SECONDS: Final = 30
MAX_RESULT_ROWS: Final = 500_000
# Memory, too (blocker 14): with only time and rows bounded, one heavy read could
# take the shared server to its ceiling, where every query fails — the workers'
# inserts included. A GROUP BY spills to disk at half the cap before it is
# refused. Sized from the development stack's 4 GiB server until the load test on
# the hosting target replaces it.
MAX_MEMORY_BYTES: Final = 1 << 30
MAX_EXTERNAL_GROUP_BY_BYTES: Final = 1 << 29

READ_SETTINGS: Final[dict[str, Any]] = {
    "max_execution_time": MAX_EXECUTION_SECONDS,
    "max_result_rows": MAX_RESULT_ROWS,
    "result_overflow_mode": "throw",
    "max_memory_usage": MAX_MEMORY_BYTES,
    "max_bytes_before_external_group_by": MAX_EXTERNAL_GROUP_BY_BYTES,
}


class TenantFilterMissingError(RuntimeError):
    """A query reached a reader without binding a tenant.

    Raised rather than silently scoped, because a query written without the
    binding was written by someone who was not thinking about tenancy, and the
    next one they write will be wrong too.
    """

    def __init__(self, sql: str) -> None:
        super().__init__(
            f"query has no `tenant_id = {TENANT_BINDING}` predicate outside of "
            f"comments and string literals, and would read across tenants: "
            f"{sql.strip()[:200]}"
        )


def executable_text(sql: str) -> str:
    """The SQL with everything ClickHouse will not act on removed."""
    return _STRING_LITERALS.sub("''", _COMMENTS.sub(" ", sql))


def binds_tenant(sql: str) -> bool:
    """Whether the statement filters on the tenant, as opposed to mentioning it."""
    return bool(TENANT_PREDICATE.search(executable_text(sql)))
