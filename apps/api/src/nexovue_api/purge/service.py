"""Deleting one tenant's telemetry from ClickHouse, and proving it happened.

**Why this is not a single statement.** `ALTER TABLE spans DELETE` is a
mutation over one table. It does not reach `span_trace_lookup`, which a
materialized view wrote from those spans, nor `trace_summaries`,
`endpoint_stats_1m`, `service_edges_1m` or `web_vitals_1m`, which the worker
writes directly — mutations do not fire materialized views, and a rollup is a
separate table either way. Every table that holds the tenant is named in
`tables.PURGED_TABLES`, and the inventory test fails if the schema grows one
this list does not.

**Submitted, then observed — not awaited.** A mutation rewrites every affected
part and can run for minutes, so blocking a request on it is not available.
The statement returns as soon as ClickHouse has recorded the obligation, which
it does durably: the mutation survives a server restart and resumes. What the
caller gets back is therefore "the store has taken this on", and completion is a
separate question with a real answer — `remaining()`, which counts the tenant's
rows in every purged table. Zero everywhere is the erasure, asked of the data
rather than of a job record.

**Failing halfway is a re-run.** If the process dies after submitting six of the
thirteen, those six complete on the server and the other seven never happened.
Re-submitting is safe and nearly free: a mutation whose predicate cannot match a
part skips it, and `tenant_id` leads the sort key of every purged table, so a
re-run against an already-purged tenant is measured at `parts_to_do = 0`. The
caller is what makes this durable — it must hold the deletion obligation in
Postgres and re-run until `remaining()` is empty. **P1-77 owns that record; it
does not exist yet.**

**A purge can be overtaken by ingest.** The receiver refuses a deleted
organization, but records it already accepted are on the broker, and a worker
writing them lands rows after the mutation has passed. So `remaining()` empty is
a statement about the moment it was read, not a permanent one, and the caller
purges again once the pipeline has drained. That is the same re-run; nothing
else is needed.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Final

from nexovue_api.data.clickhouse.base import TenantScopedPurger
from nexovue_api.purge import queries
from nexovue_api.purge.exceptions import (
    InvalidTenantError,
    PurgeFailedError,
    PurgeUnfinishedError,
)
from nexovue_api.purge.tables import PURGED_TABLES

# How often completion is re-asked while waiting. Short enough that a small
# tenant's purge is observed as finished promptly, long enough not to poll a
# store that is busy rewriting parts.
POLL_INTERVAL_SECONDS: Final = 0.5

# A mutation ClickHouse has finished reports this; anything else is in flight.
MUTATION_DONE: Final = 1


@dataclass(frozen=True, slots=True)
class MutationProgress:
    """One `ALTER TABLE ... DELETE` as `system.mutations` sees it."""

    table: str
    mutation_id: str
    is_done: bool
    is_killed: bool
    parts_to_do: int
    failure_reason: str

    @property
    def is_abandoned(self) -> bool:
        """Killed, so it will never finish without being submitted again.

        Deliberately not "has a failure reason". ClickHouse retries a mutation
        that failed and records the last attempt's reason meanwhile — a store
        briefly out of memory sets it and then succeeds. Treating that as final
        would abandon a purge that was going to complete; the reason is reported
        if the deadline passes instead.
        """
        return self.is_killed


class TenantPurge:
    """The deletion of one tenant's telemetry, from submission to proof."""

    def __init__(self, purger: TenantScopedPurger) -> None:
        _require_organization_id(purger.tenant_id)
        self._purger = purger

    @property
    def tenant_id(self) -> str:
        return self._purger.tenant_id

    async def submit(self) -> None:
        """Hand every deletion to the store. Returns once it holds them all."""
        for table in PURGED_TABLES:
            await self._purger.delete(queries.DELETE_TENANT_ROWS[table])

    async def remaining(self) -> dict[str, int]:
        """Tables that still hold rows for this tenant, and how many.

        Empty means the erasure is done. This asks the tables rather than a job
        record, so it is also the answer after a restart, a re-run, or a purge
        somebody else submitted.
        """
        rows = await self._purger.query(queries.COUNT_REMAINING_ROWS)
        return {row["table"]: int(row["remaining"]) for row in rows if int(row["remaining"]) > 0}

    async def mutations(self) -> tuple[MutationProgress, ...]:
        """Every purge mutation for this tenant the server still remembers.

        `finished_mutations_to_keep` is 100 by default, so a completed mutation
        is eventually forgotten. An empty result therefore means "nothing
        outstanding that is still recorded", never "nothing was submitted" —
        which is why `remaining()` and not this is what certifies the deletion.
        """
        rows = await self._purger.query_server_metadata(
            queries.MUTATION_PROGRESS,
            {
                "database": self._purger.database,
                "tenant_predicate": queries.STORED_TENANT_PREDICATE.format(
                    tenant_id=self.tenant_id
                ),
            },
        )
        return tuple(
            MutationProgress(
                table=str(row["table"]),
                mutation_id=str(row["mutation_id"]),
                is_done=int(row["is_done"]) == MUTATION_DONE,
                is_killed=bool(row["is_killed"]),
                parts_to_do=int(row["parts_to_do"]),
                failure_reason=str(row["latest_fail_reason"]),
            )
            for row in rows
        )

    async def run(self, *, deadline_seconds: float) -> None:
        """Submit, then wait for the store to have no rows left for this tenant.

        Raises rather than returning a status: an unfinished erasure and a
        failed one both need a caller to do something, and a boolean nobody
        checked is how a deletion obligation quietly lapses.
        """
        await self.submit()
        await self.wait_until_erased(deadline_seconds=deadline_seconds)

    async def wait_until_erased(self, *, deadline_seconds: float) -> None:
        """Poll until no purged table holds this tenant, or say why not."""
        deadline = asyncio.get_running_loop().time() + deadline_seconds
        while True:
            remaining = await self.remaining()
            if not remaining:
                return

            await self._raise_if_any_mutation_was_killed()
            if asyncio.get_running_loop().time() >= deadline:
                raise PurgeUnfinishedError(self.tenant_id, remaining, await self._last_errors())
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _raise_if_any_mutation_was_killed(self) -> None:
        """A killed mutation is the one failure waiting longer cannot fix."""
        killed = {
            progress.table: progress.failure_reason or "killed"
            for progress in await self.mutations()
            if progress.is_abandoned
        }
        if killed:
            raise PurgeFailedError(self.tenant_id, killed)

    async def _last_errors(self) -> dict[str, str]:
        """What the store last complained about, for the deadline's message."""
        return {
            progress.table: progress.failure_reason
            for progress in await self.mutations()
            if progress.failure_reason and not progress.is_done
        }


def _require_organization_id(tenant_id: str) -> None:
    """A tenant is an organization id. Anything else never reaches the store."""
    try:
        uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError) as malformed:
        raise InvalidTenantError(tenant_id) from malformed
