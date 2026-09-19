"""*New error group* (FR5, AC8): a group's first occurrence, said once.

**A baseline, then a watermark.** The first time a project has a new-error rule,
every group it has had in the retention window is recorded as seen, and nobody is
told — otherwise a rule added to a project with a history would announce every
error it ever had. After that, each minute reads the groups received since the
project's watermark.

**Read in parts.** One read returns at most `telemetry.MOST_GROUPS`, oldest first
received. A read that hits the limit moves the watermark only to its last group's
receive time, and the next minute continues from there; a baseline counts as taken
only when a read comes back whole, so no part of a history is ever announced.

**The insert is the decision.** A group is new exactly when inserting it into
`error_groups_seen` inserts a row (`ON CONFLICT DO NOTHING RETURNING`), so a sweep
that runs twice, or two that overlap, cannot announce one group twice.

**An event, not a condition.** A new group opens and closes at once: it is said
once, and there is nothing to resolve.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final

from nexovue_platform.alerts.model import NewErrorCondition, error_subject, parse_condition
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts import incidents, telemetry
from nexovue_scheduler.alerts.rules import Rule, rules_of_kinds
from nexovue_scheduler.alerts.telemetry import TelemetryStore

logger = get_logger(__name__)

KINDS: Final = ["new_error_group"]

# How far back a baseline reads: the retention window.
BASELINE: Final = timedelta(days=30)
# Rows land in the store within seconds of arriving; this is the margin before a
# minute is read as complete.
SETTLING: Final = timedelta(minutes=1)

_WATERMARK: Final = text(
    """
    SELECT received_through, baselined_at FROM error_group_watermarks
    WHERE org_id = :org_id AND project_id = :project_id
    FOR UPDATE
    """
)
_SAVE_WATERMARK: Final = text(
    """
    INSERT INTO error_group_watermarks (org_id, project_id, received_through, baselined_at)
    VALUES (:org_id, :project_id, :through, :baselined_at)
    ON CONFLICT (project_id) DO UPDATE
    SET received_through = excluded.received_through, baselined_at = excluded.baselined_at
    """
)
_MARK_SEEN: Final = text(
    """
    INSERT INTO error_groups_seen (org_id, project_id, group_id, first_seen_at)
    SELECT :org_id, :project_id, seen.group_id, seen.first_seen_at
    FROM unnest(CAST(:group_ids AS text[]), CAST(:first_seen AS timestamptz[]))
        AS seen(group_id, first_seen_at)
    ON CONFLICT (project_id, group_id) DO NOTHING
    RETURNING group_id
    """
)


async def evaluate(
    engine: AsyncEngine,
    store: TelemetryStore,
    *,
    now: datetime,
    org_id: uuid.UUID | None = None,
) -> int:
    """Every project with a new-error rule, once. Returns notifications written."""
    async with engine.connect() as connection:
        rules = await rules_of_kinds(connection, KINDS, org_id=org_id)
    by_project: dict[tuple[Any, Any], list[Rule]] = defaultdict(list)
    for rule in rules:
        by_project[(rule.org_id, rule.project_id)].append(rule)

    written = 0
    through = now - SETTLING
    for (org_id, project_id), project_rules in by_project.items():
        reader = store.for_project(org_id, project_id)
        async with engine.begin() as connection:
            keys = {"org_id": org_id, "project_id": project_id}
            position = (await connection.execute(_WATERMARK, keys)).first()
            read_from = now - BASELINE if position is None else position.received_through
            baselined_at = None if position is None else position.baselined_at
            try:
                groups = reader.error_groups(received_from=read_from, received_to=through)
            except Exception:
                logger.exception("the telemetry store could not be read; skipping this minute")
                continue
            new = await _mark_seen(connection, org_id, project_id, groups)
            whole = len(groups) < telemetry.MOST_GROUPS
            read_through = through if whole else groups[-1]["first_received"]
            await connection.execute(
                _SAVE_WATERMARK,
                keys
                | {
                    "through": read_through,
                    "baselined_at": baselined_at or (now if whole else None),
                },
            )
            if baselined_at is None:
                logger.info(
                    "new-error baseline read",
                    project_id=str(project_id),
                    groups=len(new),
                    finished=whole,
                )
                continue
            for group in (group for group in groups if group["group_id"] in new):
                written += await _announce(connection, project_rules, group, now=now)
    return written


async def _mark_seen(
    connection: AsyncConnection, org_id: Any, project_id: Any, groups: list[dict[str, Any]]
) -> set[str]:
    if not groups:
        return set()
    inserted = await connection.execute(
        _MARK_SEEN,
        {
            "org_id": org_id,
            "project_id": project_id,
            "group_ids": [str(group["group_id"]) for group in groups],
            "first_seen": [group["first_seen"] for group in groups],
        },
    )
    return {row.group_id for row in inserted}


async def _announce(
    connection: AsyncConnection, rules: list[Rule], group: dict[str, Any], *, now: datetime
) -> int:
    services = [str(service) for service in group["services"]]
    written = 0
    for rule in rules:
        try:
            condition = parse_condition(rule.condition)
        except (ValidationError, ValueError):
            logger.error("an alert rule's condition does not parse", rule_id=str(rule.id))
            continue
        if not isinstance(condition, NewErrorCondition):
            continue
        covered = [service for service in services if condition.covers(service)]
        if not covered:
            continue
        written += await incidents.judged_alone(
            connection,
            rule,
            partial(_say_once, connection, rule, condition, group, covered, now=now),
        )
    return written


async def _say_once(
    connection: AsyncConnection,
    rule: Rule,
    condition: NewErrorCondition,
    group: dict[str, Any],
    covered: list[str],
    *,
    now: datetime,
) -> int:
    subject = incidents.Subject(
        key=error_subject(str(group["group_id"])),
        label=f"{group['exception_type']} in {covered[0]}",
    )
    written = await incidents.fire(
        connection,
        rule,
        subject,
        severity=condition.severity,
        at=now,
        detail={
            "group_id": str(group["group_id"]),
            "exception_type": str(group["exception_type"]),
            "service": covered[0],
            "services": covered,
            "reading": "new error",
            "first_seen": group["first_seen"].isoformat(),
        },
    )
    # Said once; there is nothing to resolve.
    await incidents.clear(connection, rule, subject, at=now, event=True)
    return written
