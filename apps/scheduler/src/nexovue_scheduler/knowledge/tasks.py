"""F4 T5.5 and T7.2: chat's knowledge, written from people's notes and closed findings (design,
section 6).

Every 5 minutes (beat's `KNOWLEDGE_SWEEP_ENTRY`), on the shared queue with default limits,
in each organization with chat on, on an install where AI may be offered:

1. **Drops** each fact line whose finding is no longer `recovered`, unless its case was
   resolved (`knowledge_facts.kept_sql`), since a person may mark it a false alarm afterwards
   (AC84); search already leaves it out meanwhile.
2. **Copies each note** with no chunk yet into one chunk (FR30, D58), oldest first, in
   batches of `BATCH`. F22's note service does not change and a note depends on nothing here;
   the chunk cascades from its note, so deleting the note deletes it at once (BR16). **A note
   always saves:** at a project's cap the note is skipped, never refused, and the settings
   screen counts it as not searchable (FR32, AC83). Notes and runbooks come before fact lines.
3. **Writes** a fact line for each finding closed as `recovered`, or whose case a person
   resolved, with no line yet, in batches of `BATCH`, **at most one per service, signal and
   day** (FR28), from columns alone (`knowledge_facts.fact_line`). `title` and
   `feedback_reason` are never read (AC39), and no model word, log or span can reach a line.
4. **Evicts** the oldest lines past the project's share: at most `MAX_FACT_LINES`, and never
   past `MAX_PROJECT_CHUNKS` with runbooks and notes counted first, so a person's writing
   never fails because of a chat index (AC83). This sweep is the cap's one enforcement point.

A line's key is its strict service name, or none for a project finding or a name that
failed the pattern, so those share one line a signal and day. One organization's failure is
logged and the rest still run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.deployment import Deployment, ai_available
from nexovue_platform.detection import SIGNALS
from nexovue_platform.knowledge import (
    KNOWLEDGE_SWEEP_TASK,
    MAX_FACT_LINES,
    MAX_PROJECT_CHUNKS,
)
from nexovue_platform.knowledge_facts import (
    CASE_RESOLVED,
    RECOVERED,
    UNKNOWN_SIGNAL,
    fact_line,
    kept_sql,
    resolved_case_sql,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# Notes copied, and findings written, per organization per sweep (a guess, design section 6).
BATCH: Final = 200

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
_RESOLVED_CASE: Final = resolved_case_sql("finding")
# A resolved case ends when it was resolved; a recovered finding when it closed.
_FACT_ENDED: Final = (
    f"CASE WHEN {_RESOLVED_CASE} THEN finding.case_resolved_at ELSE finding.closed_at END"
)
_FACT_DAY: Final = f"(({_FACT_ENDED}) AT TIME ZONE 'UTC')::date"
_RECOVERED_FINDING: Final = (
    f"(finding.state = 'closed' AND finding.closed_reason = '{RECOVERED}' "
    "AND finding.closed_at IS NOT NULL)"
)

_CHAT_ON: Final = text(
    "SELECT settings.org_id FROM assistant_settings AS settings "
    "JOIN organizations AS organization ON organization.id = settings.org_id "
    "WHERE settings.enabled AND organization.deleted_at IS NULL ORDER BY settings.org_id"
)
_DROP_NO_LONGER_KEPT: Final = text(
    "DELETE FROM knowledge_chunks AS chunk USING findings AS finding "
    "WHERE chunk.org_id = :org_id AND chunk.source_kind = 'fact' "
    "AND finding.org_id = chunk.org_id AND finding.project_id = chunk.project_id "
    "AND finding.id = chunk.finding_id "
    f"AND NOT {kept_sql('finding')}"
)
_DUE: Final = text(
    f"""
    SELECT finding.id, finding.project_id, finding.scope, finding.services,
           finding.started_at, {_FACT_ENDED} AS ended_at, {_RESOLVED_CASE} AS case_resolved,
           {_FACT_SERVICE} AS fact_service, {_FACT_SIGNAL} AS fact_signal,
           {_FACT_DAY} AS fact_day
    FROM findings AS finding
    JOIN projects AS project
      ON project.id = finding.project_id AND project.org_id = finding.org_id
    WHERE finding.org_id = :org_id
      AND project.deleted_at IS NULL
      AND ({_RECOVERED_FINDING} OR {_RESOLVED_CASE})
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
    ORDER BY ended_at DESC, finding.id
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
# A note's chunk is its whole text (FR30); a note of a deleted project waits for the sweep
# that removes the project, and is never copied meanwhile.
_DUE_NOTES: Final = text(
    """
    SELECT note.id, finding.project_id, note.body
    FROM finding_notes AS note
    JOIN findings AS finding ON finding.org_id = note.org_id AND finding.id = note.finding_id
    JOIN projects AS project
      ON project.id = finding.project_id AND project.org_id = finding.org_id
    WHERE note.org_id = :org_id
      AND project.deleted_at IS NULL
      AND note.body <> ''
      AND NOT EXISTS (
          SELECT 1 FROM knowledge_chunks AS chunk
          WHERE chunk.org_id = note.org_id AND chunk.note_id = note.id
      )
    ORDER BY note.created_at, note.id
    LIMIT :batch
    """
)
# A note raced by another sweep writes nothing (the unique index on its key).
_INSERT_NOTE: Final = text(
    "INSERT INTO knowledge_chunks (org_id, project_id, source_kind, note_id, content) "
    "VALUES (:org_id, :project_id, 'note', :note_id, :content) ON CONFLICT DO NOTHING"
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


@dataclass(frozen=True, slots=True)
class Counts:
    dropped: int = 0
    written: int = 0
    evicted: int = 0
    notes_written: int = 0
    # Notes skipped at a project's cap: not searchable, counted on the settings screen.
    notes_skipped: int = 0


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
                    closed_at=row.ended_at,
                    ended=CASE_RESOLVED if row.case_resolved else RECOVERED,
                ),
                "fact_service": row.fact_service,
                "fact_signal": row.fact_signal,
                "fact_day": row.fact_day,
            },
        )
        written += result.rowcount
    return written


async def _person_written(connection: AsyncConnection, org_id: Any) -> dict[Any, int]:
    """Each project's chunks a person wrote: runbooks' and notes', everything but fact lines."""
    rows = (await connection.execute(_COUNTS, {"org_id": org_id})).all()
    return {row.project_id: row.total - row.facts for row in rows}


async def _copy_notes(connection: AsyncConnection, org_id: Any) -> tuple[int, int]:
    """Each due note into its own chunk while its project has room after the person-written
    chunks, never fact lines, which give way at eviction. How many were written and skipped."""
    held = await _person_written(connection, org_id)
    written = skipped = 0
    for row in (await connection.execute(_DUE_NOTES, {"org_id": org_id, "batch": BATCH})).all():
        if held.get(row.project_id, 0) >= MAX_PROJECT_CHUNKS:
            skipped += 1
            continue
        result = await connection.execute(
            _INSERT_NOTE,
            {
                "org_id": org_id,
                "project_id": row.project_id,
                "note_id": row.id,
                "content": row.body,
            },
        )
        written += result.rowcount
        held[row.project_id] = held.get(row.project_id, 0) + result.rowcount
    return written, skipped


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
    """One organization's pass, in one transaction: drop, copy notes, write, evict."""
    async with engine.begin() as connection:
        dropped = (await connection.execute(_DROP_NO_LONGER_KEPT, {"org_id": org_id})).rowcount
        notes_written, notes_skipped = await _copy_notes(connection, org_id)
        written = await _write(connection, org_id)
        evicted = await _evict(connection, org_id)
    return Counts(dropped, written, evicted, notes_written, notes_skipped)


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
        totals["notes_written"] += counts.notes_written
        totals["notes_skipped"] += counts.notes_skipped
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
