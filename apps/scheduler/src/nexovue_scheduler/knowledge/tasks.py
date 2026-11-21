"""F4 T5.5: fact lines for chat's knowledge, written from closed findings (design, section 6).

Every 5 minutes (beat's `KNOWLEDGE_SWEEP_ENTRY`), on the shared queue with default limits,
in each organization with chat on, on an install where AI may be offered:

1. **Drops** each fact line whose finding is no longer `recovered`, since a person may mark it
   a false alarm afterwards (AC84); search already leaves it out meanwhile.
2. **Writes** a line for each finding closed as `recovered` with no line yet, in batches of
   `BATCH`, **at most one per service, signal and day** (FR28), from columns alone:
   `service {name} | signal {signal} | {started} to {closed} UTC | closed: recovered`. A name
   is written only when it passes the strict pattern, else "a service"; a project finding
   names up to 5 of its services by the same rule. `title` and `feedback_reason` are never
   read (AC39), and no model word, log or span can reach a line.
3. **Evicts** the oldest lines past the project's share: at most `MAX_FACT_LINES`, and never
   past `MAX_PROJECT_CHUNKS` with runbooks counted first, so a person's writing never fails
   because of a chat index (AC83). This sweep is the cap's one enforcement point.

A line's key is its strict service name, or none for a project finding or a name that
failed the pattern, so those share one line a signal and day. One organization's failure is
logged and the rest still run. Notes and resolved cases join in Step 7 (plan T7.2).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.assistant_tools import STRICT_NAME
from nexovue_platform.deployment import Deployment, ai_available
from nexovue_platform.detection import SIGNALS
from nexovue_platform.knowledge import (
    KNOWLEDGE_SWEEP_TASK,
    MAX_FACT_LINES,
    MAX_PROJECT_CHUNKS,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# Findings written per organization per sweep (a guess, design section 6).
BATCH: Final = 200
# A project finding names at most this many of its services (FR28).
MAX_NAMED_SERVICES: Final = 5
UNNAMED_SERVICE: Final = "a service"
UNNAMED_SERVICES: Final = "several services"
UNKNOWN_SIGNAL: Final = "unknown"
_RECOVERED: Final = "recovered"

# The strict pattern, anchored, as Postgres reads it; held to `STRICT_NAME` by its test.
STRICT_NAME_SQL: Final = "^[A-Za-z0-9._-]{1,80}$"
_SIGNALS_SQL: Final = ", ".join(f"'{signal}'" for signal in SIGNALS)
# A line's key, computed once in SQL so the writer and the "already written today" check
# cannot disagree.
_FACT_SERVICE: Final = (
    f"CASE WHEN finding.service_name ~ '{STRICT_NAME_SQL}' THEN finding.service_name END"
)
_FACT_SIGNAL: Final = (
    f"CASE WHEN finding.signals->0->>'signal' IN ({_SIGNALS_SQL}) "
    f"THEN finding.signals->0->>'signal' ELSE '{UNKNOWN_SIGNAL}' END"
)
_FACT_DAY: Final = "(finding.closed_at AT TIME ZONE 'UTC')::date"

_CHAT_ON: Final = text(
    "SELECT settings.org_id FROM assistant_settings AS settings "
    "JOIN organizations AS organization ON organization.id = settings.org_id "
    "WHERE settings.enabled AND organization.deleted_at IS NULL ORDER BY settings.org_id"
)
_DROP_NO_LONGER_RECOVERED: Final = text(
    "DELETE FROM knowledge_chunks AS chunk USING findings AS finding "
    "WHERE chunk.org_id = :org_id AND chunk.source_kind = 'fact' "
    "AND finding.org_id = chunk.org_id AND finding.project_id = chunk.project_id "
    "AND finding.id = chunk.finding_id "
    f"AND finding.closed_reason IS DISTINCT FROM '{_RECOVERED}'"
)
_DUE: Final = text(
    f"""
    SELECT finding.id, finding.project_id, finding.scope, finding.services,
           finding.started_at, finding.closed_at,
           {_FACT_SERVICE} AS fact_service, {_FACT_SIGNAL} AS fact_signal,
           {_FACT_DAY} AS fact_day
    FROM findings AS finding
    JOIN projects AS project
      ON project.id = finding.project_id AND project.org_id = finding.org_id
    WHERE finding.org_id = :org_id
      AND project.deleted_at IS NULL
      AND finding.state = 'closed'
      AND finding.closed_reason = '{_RECOVERED}'
      AND finding.closed_at IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM knowledge_chunks AS chunk
          WHERE chunk.org_id = finding.org_id AND chunk.project_id = finding.project_id
            AND chunk.source_kind = 'fact'
            AND (
                chunk.finding_id = finding.id
                OR (chunk.fact_service IS NOT DISTINCT FROM {_FACT_SERVICE}
                    AND chunk.fact_signal = {_FACT_SIGNAL}
                    AND chunk.fact_day = {_FACT_DAY})
            )
      )
    ORDER BY finding.closed_at DESC, finding.id
    LIMIT :batch
    """
)
# A finding raced to the same service, signal and day writes nothing (the unique index).
_INSERT_FACT: Final = text(
    "INSERT INTO knowledge_chunks "
    "(org_id, project_id, source_kind, finding_id, content, fact_service, fact_signal, fact_day) "
    "VALUES (:org_id, :project_id, 'fact', :finding_id, :content, :fact_service, :fact_signal, "
    ":fact_day) ON CONFLICT DO NOTHING"
)
_COUNTS: Final = text(
    "SELECT project_id, count(*) FILTER (WHERE source_kind = 'fact') AS facts, "
    "count(*) AS total FROM knowledge_chunks WHERE org_id = :org_id GROUP BY project_id"
)
_EVICT_OLDEST: Final = text(
    "DELETE FROM knowledge_chunks WHERE org_id = :org_id AND id IN ("
    "SELECT id FROM knowledge_chunks WHERE org_id = :org_id AND project_id = :project_id "
    "AND source_kind = 'fact' ORDER BY fact_day, created_at, id LIMIT :excess)"
)


def _strict(name: object) -> str | None:
    return name if isinstance(name, str) and STRICT_NAME.fullmatch(name) else None


def _minute(moment: datetime) -> str:
    """In UTC whatever the connection's zone, as the line says."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def fact_line(
    *,
    scope: str,
    fact_service: str | None,
    services: object,
    signal: str,
    started_at: datetime,
    closed_at: datetime,
) -> str:
    """A recovered finding as one line, from columns alone (design, section 6; AC39)."""
    if scope == "project":
        names = [name for item in _items(services) if (name := _strict(item))]
        named = ", ".join(dict.fromkeys(names[:MAX_NAMED_SERVICES])) or UNNAMED_SERVICES
        who = f"services {named}"
    else:
        who = f"service {_strict(fact_service) or UNNAMED_SERVICE}"
    return (
        f"{who} | signal {signal} | {_minute(started_at)} to {_minute(closed_at)} UTC "
        f"| closed: {_RECOVERED}"
    )


def _items(services: object) -> list[object]:
    return list(services) if isinstance(services, list) else []


@dataclass(frozen=True, slots=True)
class Counts:
    dropped: int = 0
    written: int = 0
    evicted: int = 0


async def _write(connection: AsyncConnection, org_id: Any) -> int:
    written = 0
    for row in (await connection.execute(_DUE, {"org_id": org_id, "batch": BATCH})).all():
        result = await connection.execute(
            _INSERT_FACT,
            {
                "org_id": org_id,
                "project_id": row.project_id,
                "finding_id": row.id,
                "content": fact_line(
                    scope=row.scope,
                    fact_service=row.fact_service,
                    services=row.services,
                    signal=row.fact_signal,
                    started_at=row.started_at,
                    closed_at=row.closed_at,
                ),
                "fact_service": row.fact_service,
                "fact_signal": row.fact_signal,
                "fact_day": row.fact_day,
            },
        )
        written += result.rowcount
    return written


def fact_lines_kept(*, facts: int, total: int) -> int:
    """How many fact lines a project keeps: its share, after everything else it holds."""
    return max(0, min(MAX_FACT_LINES, MAX_PROJECT_CHUNKS - (total - facts)))


async def _evict(connection: AsyncConnection, org_id: Any) -> int:
    evicted = 0
    for row in (await connection.execute(_COUNTS, {"org_id": org_id})).all():
        excess = row.facts - fact_lines_kept(facts=row.facts, total=row.total)
        if excess > 0:
            result = await connection.execute(
                _EVICT_OLDEST,
                {"org_id": org_id, "project_id": row.project_id, "excess": excess},
            )
            evicted += result.rowcount
    return evicted


async def sweep_organization(engine: AsyncEngine, org_id: Any) -> Counts:
    """One organization's pass, in one transaction: drop, write, evict."""
    async with engine.begin() as connection:
        dropped = (await connection.execute(_DROP_NO_LONGER_RECOVERED, {"org_id": org_id})).rowcount
        written = await _write(connection, org_id)
        evicted = await _evict(connection, org_id)
    return Counts(dropped, written, evicted)


async def sweep(engine: AsyncEngine, *, deployment: Deployment) -> dict[str, int]:
    """One pass over every organization with chat on; how many lines went each way."""
    totals: Counter[str] = Counter()
    if not ai_available(deployment):
        return dict(totals)
    async with engine.connect() as connection:
        organizations = list((await connection.execute(_CHAT_ON)).scalars())
    for org_id in organizations:
        try:
            counts = await sweep_organization(engine, org_id)
        except SQLAlchemyError as error:
            reason = repr(getattr(error, "orig", error))
            logger.error(
                "an organization's knowledge sweep failed", org_id=str(org_id), error=reason
            )
            totals["failed"] += 1
            continue
        totals["dropped"] += counts.dropped
        totals["written"] += counts.written
        totals["evicted"] += counts.evicted
    return dict(totals)


@task(KNOWLEDGE_SWEEP_TASK)
def sweep_knowledge() -> None:
    deployment = get_settings().nexovue_deployment
    counted = {
        name: count
        for name, count in with_engine(lambda engine: sweep(engine, deployment=deployment)).items()
        if count
    }
    if counted:
        logger.info("knowledge swept", **counted)
