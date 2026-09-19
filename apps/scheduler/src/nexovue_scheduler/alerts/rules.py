"""Reading a project's alert rules, with the channels each sends to.

Plain SQL over named columns, as the rest of the scheduler reads the API's
tables (`checks.py`): the two deploy separately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_ENABLED_RULES: Final = text(
    """
    SELECT alert_rules.id, alert_rules.name, alert_rules.condition, alert_rules.muted_until,
           coalesce(
               array_agg(alert_rule_channels.channel_id)
                   FILTER (WHERE alert_rule_channels.channel_id IS NOT NULL),
               '{}'
           ) AS channel_ids
    FROM alert_rules
    LEFT JOIN alert_rule_channels ON alert_rule_channels.rule_id = alert_rules.id
    WHERE alert_rules.org_id = :org_id
      AND alert_rules.project_id = :project_id
      AND alert_rules.condition_kind = :condition_kind
      AND alert_rules.enabled
    GROUP BY alert_rules.id
    ORDER BY alert_rules.id
    """
)


@dataclass(frozen=True, slots=True)
class Rule:
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    condition_kind: str
    condition: dict[str, Any]
    muted_until: datetime | None
    channel_ids: tuple[uuid.UUID, ...]

    def muted_at(self, at: datetime) -> bool:
        """A mute ends by itself (FR14): past its end, the rule is heard again."""
        return self.muted_until is not None and self.muted_until > at


async def enabled_rules(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    condition_kind: str,
) -> list[Rule]:
    rows = await connection.execute(
        _ENABLED_RULES,
        {"org_id": org_id, "project_id": project_id, "condition_kind": condition_kind},
    )
    return [
        Rule(
            id=row.id,
            org_id=org_id,
            project_id=project_id,
            name=row.name,
            condition_kind=condition_kind,
            condition=row.condition,
            muted_until=row.muted_until,
            channel_ids=tuple(row.channel_ids),
        )
        for row in rows
    ]


# Across organizations on purpose: a sweep evaluates every rule of its kinds, and
# each row carries the `org_id` and `project_id` everything it writes is scoped by.
_RULES_OF_KINDS: Final = text(
    """
    SELECT alert_rules.id, alert_rules.org_id, alert_rules.project_id, alert_rules.name,
           alert_rules.condition_kind, alert_rules.condition, alert_rules.muted_until,
           coalesce(
               array_agg(alert_rule_channels.channel_id)
                   FILTER (WHERE alert_rule_channels.channel_id IS NOT NULL),
               '{}'
           ) AS channel_ids
    FROM alert_rules
    JOIN projects
        ON projects.id = alert_rules.project_id AND projects.org_id = alert_rules.org_id
    LEFT JOIN alert_rule_channels ON alert_rule_channels.rule_id = alert_rules.id
    WHERE alert_rules.enabled
      AND alert_rules.condition_kind = ANY(CAST(:kinds AS text[]))
      AND projects.deleted_at IS NULL
      AND (CAST(:org_id AS uuid) IS NULL OR alert_rules.org_id = CAST(:org_id AS uuid))
    GROUP BY alert_rules.id
    ORDER BY alert_rules.project_id, alert_rules.id
    """
)


async def rules_of_kinds(
    connection: AsyncConnection, kinds: list[str], *, org_id: uuid.UUID | None = None
) -> list[Rule]:
    """Every enabled rule of these kinds — or one organization's, to evaluate a
    single tenant on its own."""
    rows = await connection.execute(_RULES_OF_KINDS, {"kinds": kinds, "org_id": org_id})
    return [
        Rule(
            id=row.id,
            org_id=row.org_id,
            project_id=row.project_id,
            name=row.name,
            condition_kind=row.condition_kind,
            condition=row.condition,
            muted_until=row.muted_until,
            channel_ids=tuple(row.channel_ids),
        )
        for row in rows
    ]
