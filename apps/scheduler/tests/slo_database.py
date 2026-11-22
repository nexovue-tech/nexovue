"""The F19 job suites' Postgres: a database of their own on the dev stack's server.

These suites run the real `judge_slos` at minutes of their own choosing. On the stack's
database the stack's detection sweep hands the same job out every minute for every project
with a live target, a test's too, and its `slo` worker judges it at the real settled minute
(`count_slos` likewise counts its days). A judging holds the project's session lock and a
minute not newer than the last is skipped, both by design (AC66), so whenever the stack's
run lands beside a test's, the test's judgings are skipped and the test reads the stack's
work, not its own (CI run 123). `jobs_held` cannot help: the stack queues those jobs on its
own connections. In a database of the suite's own no stack sweep or worker looks, and an
advisory lock belongs to one database, so the suite alone judges its targets
(`tests/helpers/private_database.py`).

A suite declares a module-scoped `database` fixture from `slo_database()` and an `engine`
fixture from `engine_on(database)`; every shared fixture built on `engine` (`tenant`,
`project`) then follows it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Final

from private_database import private_database
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)


@contextmanager
def slo_database() -> Iterator[str]:
    """A database of the suite's own, migrated by the real `setup`; dropped on exit."""
    with private_database(POSTGRES_DSN, prefix="nexovue_slo") as dsn:
        yield dsn


@asynccontextmanager
async def engine_on(database: str) -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(database, pool_size=4)
    try:
        yield created
    finally:
        await created.dispose()
