"""Test tenants' ClickHouse rows, deleted once at the end of the run, not after each test.

`ALTER TABLE ... DELETE` is a mutation, and a tenant is not a partition: every one
writes a new generation of *every* part in its table, and ClickHouse keeps each
superseded part — with its per-file checksums and column metadata, large for the
JSON attribute columns — in memory for `old_parts_lifetime` (480 s). Suites that
emptied their tenant after every test issued dozens of mutations per table inside
that window. Measured 2026-09-19 (docs/changes/gate-found-defects/): 12,900
superseded parts held 3.3 GiB against the development cap of 4 GiB, and queries
failed MEMORY_LIMIT_EXCEEDED; as they expired, allocation fell to 1.5 GiB.

A test tenant belongs to one test, so nothing reads its rows afterwards; they only
have to be gone eventually. `delete_after_run` records them, and the end of the
session issues one mutation per table for all of them, without waiting on it.

Registered for every suite with `-p clickhouse_cleanup` (pyproject.toml).
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import Any, Final
from urllib.parse import urlsplit

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)

_pending: dict[str, set[str]] = defaultdict(set)


def delete_after_run(table: str, tenant_id: str) -> None:
    """Delete this tenant's rows from `table` when the test session ends."""
    _pending[table].add(tenant_id)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not _pending:
        return
    import clickhouse_connect

    url = urlsplit(CLICKHOUSE_DSN)
    try:
        client = clickhouse_connect.get_client(
            host=url.hostname or "localhost",
            port=url.port or 8123,
            username=url.username or "",
            password=url.password or "",
            database=(url.path or "/").lstrip("/"),
        )
        for table, tenants in sorted(_pending.items()):
            client.command(
                f"ALTER TABLE {table} DELETE WHERE tenant_id IN %(tenants)s",
                parameters={"tenants": sorted(tenants)},
                settings={"mutations_sync": 0},
            )
        client.close()
    except Exception as failure:
        # Said, not raised: a raise here is an INTERNALERROR over a result the
        # tests already earned. The rows are a test tenant's; the next run's
        # cleanup does not depend on them.
        sys.stderr.write(f"\nclickhouse_cleanup: test tenants' rows were not deleted: {failure}\n")
    _pending.clear()
