"""The erasure's way into the telemetry store, and the only one that can delete.

Holds the raw client, as `alerts/telemetry.py` does for reads. What it will run
is narrow: a statement from `statements.py` that binds the owed tenants, against
an `OwedTenants` list that is not empty, or a read of `system.mutations`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from nexovue_platform.tenant_statements import executable_text

from nexovue_scheduler.erasure import statements
from nexovue_scheduler.erasure.owed import OwedTenants

# "Record the mutation and return": the server holds it durably and resumes it
# after a restart, so completion is observed rather than waited on in one call.
MUTATIONS_ASYNCHRONOUS: Final = 0

# How often a mutation in flight is asked about. A night's run waits minutes
# per table; a second is plenty.
POLL_SECONDS: Final = 1.0


class ErasureRefusedError(RuntimeError):
    """A statement or a tenant list this store will not run."""


class MutationKilledError(RuntimeError):
    """Somebody killed the mutation; it will not finish unless submitted again."""

    def __init__(self, table: str, reason: str) -> None:
        super().__init__(f"the erasure mutation on {table} was killed: {reason or 'no reason'}")
        self.table = table


@dataclass(frozen=True, slots=True)
class Remaining:
    """Rows still held for one owed tenant, by table. Empty means erased."""

    org_id: str
    tables: dict[str, int]


class ErasureStore:
    def __init__(self, client: Client) -> None:
        self._client = client

    def submit(self, table: str, owed: OwedTenants) -> None:
        """Hand one table's mutation to the server. Returns once it holds it."""
        self._client.command(
            _checked(statements.DELETE_OWED_ROWS[table], owed),
            parameters={"owed_tenants": list(owed.ids)},
            settings={"mutations_sync": MUTATIONS_ASYNCHRONOUS},
        )

    def wait_for(self, table: str) -> None:
        """Until no erasure mutation on `table` is in flight. Bounded by the job's
        soft time limit, which interrupts the sleep."""
        while True:
            rows = self._client.query(
                statements.MUTATIONS_IN_FLIGHT, parameters={"table": table}
            ).result_rows
            if not rows:
                return
            for _mutation_id, is_killed, reason in rows:
                if is_killed:
                    raise MutationKilledError(table, str(reason))
            time.sleep(POLL_SECONDS)

    def remaining(self, owed: OwedTenants) -> list[Remaining]:
        """Every owed tenant's rows left, per table. Asked of the data, not a job."""
        held: dict[str, dict[str, int]] = {org_id: {} for org_id in owed.ids}
        result = self._client.query(
            _checked(statements.COUNT_OWED_ROWS, owed),
            parameters={"owed_tenants": list(owed.ids)},
        )
        for table, tenant_id, count in result.result_rows:
            if int(count) > 0:
                held.setdefault(str(tenant_id), {})[str(table)] = int(count)
        return [Remaining(org_id=org_id, tables=tables) for org_id, tables in held.items()]


def connect(dsn: str) -> ErasureStore:
    """The store at `clickhouse://user:password@host:port/database`."""
    parts = urlsplit(dsn)
    client = clickhouse_connect.get_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        database=parts.path.lstrip("/") or "default",
        secure=parts.scheme in ("https", "clickhouses"),
    )
    return ErasureStore(client)


def _checked(sql: str, owed: OwedTenants) -> str:
    """Refuse an empty list — `IN []` matches nothing and would certify nothing
    erased — and any statement that does not bind the owed tenants."""
    if not owed.ids:
        raise ErasureRefusedError("no owed tenants to erase")
    if not statements.OWED_TENANTS_PREDICATE.search(executable_text(sql)):
        raise ErasureRefusedError(f"the statement does not bind the owed tenants: {sql[:200]}")
    return sql
