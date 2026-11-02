"""The dev stack's own project: the one `just seed` creates and `tools/gateseed` sends to.

Suites that assert on real instrumentation name this project rather than taking the
busiest one, because other suites seed synthetic history into the same shared tables
(the detection suites write weeks of it) and the busiest project is then theirs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Final

import asyncpg

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
# `nexovue_api.seed`'s DEV_ORG_SLUG and DEV_PROJECT_SLUG; this helper sits outside the
# apps, so it repeats them rather than import the API package into the scheduler's tests.
DEV_ORG_SLUG: Final = "acme"
DEV_PROJECT_SLUG: Final = "production"

_QUERY: Final = """
    SELECT organizations.id::text, projects.id::text
    FROM organizations JOIN projects ON projects.org_id = organizations.id
    WHERE organizations.slug = $1 AND projects.slug = $2
"""


async def dev_project_async() -> tuple[str, str]:
    """The dev organization's id (the ClickHouse `tenant_id`) and its project's id."""
    connection = await asyncpg.connect(POSTGRES_DSN)
    try:
        row = await connection.fetchrow(_QUERY, DEV_ORG_SLUG, DEV_PROJECT_SLUG)
    finally:
        await connection.close()
    if row is None:
        raise AssertionError(
            f"no {DEV_ORG_SLUG}/{DEV_PROJECT_SLUG} project in Postgres; run `just seed`, then "
            "`just gate-seed` for its traffic"
        )
    return str(row[0]), str(row[1])


def dev_project() -> tuple[str, str]:
    """`dev_project_async` for a synchronous suite."""
    return asyncio.run(dev_project_async())
