"""Holding a live test's jobs from the running stack's own workers.

The dev stack's workers poll the shared `jobs` table every second and claim any due job on
their queues, whichever organization it is for. A test that reads back, or runs itself, a job
the code under test adds would otherwise race them: the stack may run the job its own way,
change the rows the test is about, and finish (delete) the job before the test reads it.

`jobs_held(engine, org_id)` holds every job of that organization still due when a transaction
on `engine` commits, inside that transaction, just before its commit: the job's `run_after`
becomes `infinity`, so no worker's claim ever finds it due. The row is otherwise unchanged, and
it still counts as waiting unclaimed for a conditional insert (`ENQUEUE_UNLESS_WAITING`). The
organization's removal deletes it. It works however the job was added: `queue.enqueue`, a
statement, or a database function.

Code running as the AI role, which holds no right on `jobs`, is held through `acting_as`: an
engine signed in as the install's account whose every connection acts as the AI role, as
`test_insight_account_live` does, and steps out of it only to hold the jobs.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_HOLD: Final = text(
    "UPDATE jobs SET run_after = 'infinity' "
    "WHERE org_id = CAST(:org_id AS uuid) AND run_after <> 'infinity'"
)
_ACTING_AS: Final = text("SELECT current_user <> session_user AS acting, current_user AS role")
_STEP_OUT: Final = text("RESET ROLE")
_ACT_AS: Final = "SELECT set_config('role', $1, false)"


@contextmanager
def jobs_held(engine: AsyncEngine, org_id: uuid.UUID | str) -> Iterator[None]:
    """While open, every commit on `engine` first holds `org_id`'s due jobs."""

    def hold(connection: Connection) -> None:
        acting = connection.execute(_ACTING_AS).one()
        if acting.acting:
            connection.execute(_STEP_OUT)
        connection.execute(_HOLD, {"org_id": str(org_id)})
        if acting.acting:
            connection.exec_driver_sql(_ACT_AS, (acting.role,))

    event.listen(engine.sync_engine, "commit", hold)
    try:
        yield
    finally:
        event.remove(engine.sync_engine, "commit", hold)


def acting_as(install_dsn: str, role: str, **options: Any) -> AsyncEngine:
    """An engine signed in as the install's account whose every connection acts as `role`, so
    `role`'s grants decide each statement and `jobs_held` can still hold its jobs."""
    built = create_async_engine(install_dsn, **options)

    @event.listens_for(built.sync_engine, "connect")
    def act(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(_ACT_AS, (role,))
        cursor.close()

    return built
