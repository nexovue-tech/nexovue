"""*Service went silent* (FR5, AC10): no telemetry of any kind from a service that
sent some in the last day.

Read from Postgres, not ClickHouse: the usage meter already records, every ten
seconds, when each service was last heard (`service_last_seen`).

**A stalled meter is not a silent service** (design, *Service went silent*; plan Q2).
Silence is judged against the clock, but first this asks whether the meter has
fallen behind — data waiting in Kafka that it has not read, and no progress for
five minutes. If so, every service would look silent at once, so the sweep opens
nothing and says why. A meter that is simply idle, because nothing is arriving, is
not behind: there is nothing unread, and a service that stopped is really silent.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final, Protocol

from nexovue_platform.alerts.model import (
    SILENCE_LOOKBACK_HOURS,
    SilenceCondition,
    parse_condition,
    service_subject,
)
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule, rules_of_kinds

logger = get_logger(__name__)

KINDS: Final = ["service_silent"]

# How long the meter may make no progress while data waits before it is behind.
METER_STALL: Final = timedelta(minutes=5)

# Across organizations on purpose: the meter serves every tenant from one set of
# partitions, and that set is what "behind" is a question about.
_METER_PROGRESS: Final = text(
    """
    SELECT topic, partition_id, counted_through, counted_through_received_at
    FROM usage_ingest_offsets
    """
)
_LAST_SEEN: Final = text(
    """
    SELECT service_name, last_seen_at FROM service_last_seen
    WHERE org_id = :org_id AND project_id = :project_id AND last_seen_at >= :since
    """
)


class Watermarks(Protocol):
    """The newest offset of a partition — Kafka's high watermark, minus one."""

    def newest_offset(self, topic: str, partition: int) -> int: ...


@dataclass(frozen=True, slots=True)
class MeterProgress:
    topic: str
    partition: int
    counted_through: int
    received_through: datetime | None


def meter_is_behind(
    progress: list[MeterProgress], watermarks: Watermarks, *, now: datetime
) -> bool:
    """Data is waiting that the meter has not read, and it has not moved for a while.

    A partition with nothing unread is caught up however long ago it last moved:
    that is a quiet system, not a stuck meter."""
    for partition in progress:
        unread = watermarks.newest_offset(partition.topic, partition.partition) > (
            partition.counted_through
        )
        stalled = (
            partition.received_through is None or now - partition.received_through > METER_STALL
        )
        if unread and stalled:
            return True
    return False


async def meter_progress(engine: AsyncEngine) -> list[MeterProgress]:
    async with engine.connect() as connection:
        rows = await connection.execute(_METER_PROGRESS)
        return [
            MeterProgress(
                topic=row.topic,
                partition=row.partition_id,
                counted_through=row.counted_through,
                received_through=row.counted_through_received_at,
            )
            for row in rows
        ]


async def evaluate(engine: AsyncEngine, *, now: datetime, org_id: uuid.UUID | None = None) -> int:
    """Every silence rule, once. The caller has already asked whether the meter is
    behind. Returns how many notifications were written."""
    async with engine.connect() as connection:
        rules = await rules_of_kinds(connection, KINDS, org_id=org_id)
    by_project: dict[tuple[Any, Any], list[Any]] = defaultdict(list)
    for rule in rules:
        by_project[(rule.org_id, rule.project_id)].append(rule)

    written = 0
    since = now - timedelta(hours=SILENCE_LOOKBACK_HOURS)
    for (org_id, project_id), project_rules in by_project.items():
        async with engine.begin() as connection:
            heard = {
                row.service_name: row.last_seen_at
                for row in await connection.execute(
                    _LAST_SEEN, {"org_id": org_id, "project_id": project_id, "since": since}
                )
            }
            for rule in project_rules:
                try:
                    condition = parse_condition(rule.condition)
                except (ValidationError, ValueError):
                    logger.error("an alert rule's condition does not parse", rule_id=str(rule.id))
                    continue
                if not isinstance(condition, SilenceCondition):
                    continue
                for service, last_seen in sorted(heard.items()):
                    if condition.covers(service):
                        written += await incidents.judged_alone(
                            connection,
                            rule,
                            partial(
                                _judge_one, connection, rule, condition, service, last_seen, now=now
                            ),
                        )
    return written


async def _judge_one(
    connection: AsyncConnection,
    rule: Rule,
    condition: SilenceCondition,
    service: str,
    last_seen: datetime,
    *,
    now: datetime,
) -> int:
    subject = incidents.Subject(key=service_subject(service), label=service)
    if now - last_seen < timedelta(minutes=condition.silent_minutes):
        return await incidents.clear(connection, rule, subject, at=now)
    return await incidents.fire(
        connection,
        rule,
        subject,
        severity=condition.severity,
        at=now,
        detail={
            "service": service,
            "reading": "silence",
            "last_seen": last_seen.isoformat(),
            "silent_minutes": condition.silent_minutes,
        },
    )
