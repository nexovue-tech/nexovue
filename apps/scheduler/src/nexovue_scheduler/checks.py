"""Reading check definitions out of Postgres.

The `checks` table is owned by the API, which is where customers create and edit
rows. This service only reads it, and reads it by name rather than importing the
API's SQLAlchemy models: the two deploy separately, and a scheduler that cannot
start because a control-plane package moved is a scheduler that is not running
anyone's checks. The same reasoning already puts the Kafka topic names in each
service's own settings.

The cost of that choice is drift — a column renamed in the API breaks this query
at runtime rather than at import. The query names its columns explicitly so the
failure is loud and specific, and `test_checks.py` runs it against the real
schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Only enabled rows, and only the columns the scheduler needs. `interval_seconds`
# is validated here rather than trusted: it reaches Celery as a schedule, and a
# zero would spin.
_ENABLED_CHECKS: Final = text(
    """
    SELECT id, org_id, project_id, type, target, interval_seconds, config
    FROM checks
    WHERE enabled = true
    ORDER BY id
    """
)


@dataclass(frozen=True, slots=True)
class CheckDefinition:
    """One row of `checks`, as the scheduler sees it.

    Carries `org_id` and `project_id` because the probe that runs from it has to
    stamp them on the envelope — a measurement that reaches the broker without a
    tenant is a measurement nothing can attribute or isolate.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    type: str
    target: str
    interval_seconds: int
    config: dict[str, Any]

    @property
    def schedule_name(self) -> str:
        """The name this check goes by in the beat schedule.

        The row id, not the target: two checks may watch the same URL at
        different intervals, and a name collision would silently drop one.
        """
        return f"check:{self.id}"


async def load_enabled_checks(engine: AsyncEngine) -> list[CheckDefinition]:
    """Every enabled check, for one refresh of the beat schedule.

    A row with a non-positive interval is skipped rather than raising. One bad
    row must not stop every other customer's checks from being scheduled, and
    the API validates the column on write, so reaching here means something got
    past it.
    """
    async with engine.connect() as connection:
        rows = (await connection.execute(_ENABLED_CHECKS)).mappings().all()

    return [
        CheckDefinition(
            id=row["id"],
            org_id=row["org_id"],
            project_id=row["project_id"],
            type=row["type"],
            target=row["target"],
            interval_seconds=row["interval_seconds"],
            config=row["config"] or {},
        )
        for row in rows
        if row["interval_seconds"] > 0
    ]
