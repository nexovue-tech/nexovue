"""The evidence job: read one finding's facts, choose the likely cause, freeze a version
(F3 design, *Facts: when and how*; plan T1.6).

1. A session lock on the finding, so two gathers of it never overlap and a later
   version never holds older facts (AC3). Held: `Retry` in 30 seconds.
2. The finding and its project, by `org_id` and id. A deleted finding or project ends it.
3. Each read on its own, under lowered bounds. A read that fails is remembered and
   becomes a `could_not_read` absence, never a zero (AC64), and the earlier version's
   facts of that kind are carried, marked as read then.
4. `rules.decide`, then `sentences.record`, then `inputs.build` (aliases, model input).
5. The fingerprint is identities and conclusions only (FR43). The same as the latest
   version's: nothing is written. Otherwise version = latest + 1, in one transaction.
6. A failed read asks for one re-read in 60 seconds (`retry_read`), never a repeat of
   the whole job, so a failing read in a wide outage cannot triple the work.

No AI code is imported here, directly or not (AC5). In Step 1 AI wording does not
exist, so every version is written `off`; Step 5 adds the wording decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform.deployment import SELF_HOSTED, Deployment
from nexovue_platform.evidence import (
    GATHER_EVIDENCE_TASK,
    LOCK_RETRY_SECONDS,
    PERSON,
    RETRY_READ,
    RETRY_READ_DELAY_SECONDS,
    ReadReason,
    gather_evidence_request,
)
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.evidence import inputs, reads, rules, sentences
from nexovue_scheduler.evidence.rules import Facts, FindingInput
from nexovue_scheduler.evidence.store import (
    EvidenceReader,
    EvidenceStore,
    connect_store,
    groups_for,
)
from nexovue_scheduler.jobs import queue
from nexovue_scheduler.jobs.registry import Retry, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

_TRY_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK: Final = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
_INSERT_VERSION: Final = text(
    """
    INSERT INTO finding_explanations (
        org_id, project_id, finding_id, version, read_at, fingerprint, reads_failed,
        evidence, aliases, likely_cause, summary, model_input, wording_state
    )
    VALUES (
        :org_id, :project_id, :finding_id, :version, :read_at, :fingerprint, :reads_failed,
        CAST(:evidence AS jsonb), CAST(:aliases AS jsonb), CAST(:likely_cause AS jsonb),
        CAST(:summary AS jsonb), CAST(:model_input AS jsonb), :wording_state
    )
    ON CONFLICT (finding_id, version) DO NOTHING
    """
)
# The hour's error-group read, reused inside its minute (A62).
_CACHED_GROUPS: Final = text(
    """
    SELECT groups FROM evidence_error_group_reads
    WHERE org_id = :org_id AND project_id = :project_id AND read_minute = :read_minute
      AND created_at >= :fresh_from
    """
)
_CACHE_GROUPS: Final = text(
    """
    INSERT INTO evidence_error_group_reads (org_id, project_id, read_minute, groups, created_at)
    VALUES (:org_id, :project_id, :read_minute, CAST(:groups AS jsonb), now())
    ON CONFLICT (org_id, project_id, read_minute)
    DO UPDATE SET groups = EXCLUDED.groups, created_at = EXCLUDED.created_at
    """
)
# Which kinds of fact each read gives, for carrying an earlier version's when it fails.
_KINDS_OF_READ: Final = {
    "deploys": ("deploy",),
    "dependencies": ("dependency",),
    "traffic": ("traffic",),
    "errors": ("error_group",),
    "checks": ("check",),
    "incidents": ("incident",),
    "findings": ("other_finding",),
    "hosts": ("infrastructure",),
}
# The finding's window runs from its start to its close, or to now (design, *Links*).
_MINUTE: Final = timedelta(minutes=1)
# In Step 1 there is no AI wording (plan Step 5), so every version is written `off`.
WORDING_OFF: Final = "off"


@dataclass(frozen=True, slots=True)
class Gathered:
    """What one gather did: a new version's number, or None when nothing was new."""

    version: int | None
    reads_failed: int
    likely_rule: str | None


def lock_key(finding_id: uuid.UUID) -> str:
    return f"evidence:{finding_id}"


async def gather(
    engine: AsyncEngine,
    store: EvidenceStore | None,
    *,
    finding_id: uuid.UUID,
    org_id: uuid.UUID,
    reason: ReadReason,
    deployment: Deployment,
    now: datetime | None = None,
) -> Gathered | None:
    """One read of one finding. None when the finding or its project is gone."""
    async with engine.connect() as lock:
        held = bool(await lock.scalar(_TRY_LOCK, {"key": lock_key(finding_id)}))
        await lock.commit()
        if not held:
            logger.info("evidence_lock_held", finding_id=str(finding_id), reason=reason)
            raise Retry(LOCK_RETRY_SECONDS, "another gather holds this finding")
        try:
            return await _gather_locked(
                engine,
                lock,
                store,
                finding_id=finding_id,
                org_id=org_id,
                reason=reason,
                deployment=deployment,
                now=now or datetime.now(UTC),
            )
        finally:
            # The hour's error-group write shares this connection; a failed one leaves its
            # transaction aborted, and the unlock must still run.
            await lock.rollback()
            await lock.execute(_UNLOCK, {"key": lock_key(finding_id)})
            await lock.commit()


async def _gather_locked(
    engine: AsyncEngine,
    lock: AsyncConnection,
    store: EvidenceStore | None,
    *,
    finding_id: uuid.UUID,
    org_id: uuid.UUID,
    reason: ReadReason,
    deployment: Deployment,
    now: datetime,
) -> Gathered | None:
    async with engine.connect() as connection:
        await connection.begin()
        row = await reads.finding(connection, org_id=org_id, finding_id=finding_id)
        if row is None or row.project_deleted:
            return None
        latest = await reads.latest_version(connection, org_id=org_id, finding_id=finding_id)
        previous_items = await _previous_items(connection, org_id, finding_id) if latest else []
        facts = await read_facts(connection, store, row, deployment=deployment, now=now, cache=lock)
        await connection.rollback()
    cause = rules.decide(facts)
    record = sentences.record(facts, cause)
    items = _with_carried(record.items, previous_items, facts.failed_reads)
    book = AliasBook(None if latest is None else latest.aliases)
    model_input = inputs.build(
        finding={
            "service_name": row.service_name,
            "signals": row.signals,
            "severity": row.severity,
            "started_at": row.started_at,
        },
        items=items,
        likely_cause=None if cause is None else cause.as_json(),
        book=book,
        now=now,
    )
    printed = fingerprint(items, cause)
    retry = bool(facts.failed_reads) and reason != RETRY_READ
    if latest is not None and latest.fingerprint == printed:
        if reason == PERSON:
            logger.info("evidence_nothing_new", finding_id=str(finding_id))
        if retry:
            await _enqueue_retry(engine, finding_id, org_id)
        return Gathered(version=None, reads_failed=record.reads_failed, likely_rule=_rule(cause))
    version = 1 if latest is None else latest.version + 1
    async with engine.begin() as connection:
        await connection.execute(
            _INSERT_VERSION,
            {
                "org_id": org_id,
                "project_id": row.project_id,
                "finding_id": finding_id,
                "version": version,
                "read_at": now,
                "fingerprint": printed,
                "reads_failed": record.reads_failed,
                "evidence": json.dumps({"items": items}),
                "aliases": json.dumps(book.as_json()),
                "likely_cause": None if cause is None else json.dumps(cause.as_json()),
                "summary": json.dumps(record.summary),
                "model_input": json.dumps(model_input.as_json()),
                "wording_state": WORDING_OFF,
            },
        )
        if retry:
            await queue.enqueue(
                connection,
                gather_evidence_request(
                    finding_id, org_id, RETRY_READ, countdown=RETRY_READ_DELAY_SECONDS
                ),
            )
    return Gathered(version=version, reads_failed=record.reads_failed, likely_rule=_rule(cause))


def _rule(cause: rules.Cause | None) -> str | None:
    return None if cause is None else cause.rule


async def _enqueue_retry(engine: AsyncEngine, finding_id: uuid.UUID, org_id: uuid.UUID) -> None:
    async with engine.begin() as connection:
        await queue.enqueue(
            connection,
            gather_evidence_request(
                finding_id, org_id, RETRY_READ, countdown=RETRY_READ_DELAY_SECONDS
            ),
        )


_PREVIOUS_EVIDENCE: Final = text(
    """
    SELECT evidence, read_at FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id
    ORDER BY version DESC LIMIT 1
    """
)


async def _previous_items(
    connection: AsyncConnection, org_id: uuid.UUID, finding_id: uuid.UUID
) -> list[dict[str, Any]]:
    row = (
        await connection.execute(_PREVIOUS_EVIDENCE, {"org_id": org_id, "finding_id": finding_id})
    ).first()
    if row is None:
        return []
    read_at = row.read_at.isoformat()
    return [
        {**item, "read_at": item.get("read_at", read_at)} for item in row.evidence.get("items", [])
    ]


def _with_carried(
    items: list[dict[str, Any]], previous: list[dict[str, Any]], failed: frozenset[str]
) -> list[dict[str, Any]]:
    """A failed read keeps the earlier version's facts of that kind, marked as read then
    (design, gather step 9): a read that failed this time is not news that they went."""
    kinds = {kind for read in failed for kind in _KINDS_OF_READ.get(read, ())}
    if not kinds:
        return items
    present = {item["id"] for item in items}
    carried = [
        item
        for item in previous
        if item["kind"] in kinds and item["id"] not in present and "role" not in item
    ]
    absences = [item for item in items if item["kind"] == "absence"]
    return [*(item for item in items if item["kind"] != "absence"), *carried, *absences]


def fingerprint(items: list[dict[str, Any]], cause: rules.Cause | None) -> str:
    """FR43: identities and conclusions only. Moved counts never make a version."""
    identity = {
        "facts": sorted(item["id"] for item in items),
        "cause": None
        if cause is None
        else [
            cause.evidence_id,
            cause.rule,
            cause.strength,
            sorted(cause.supported_by),
            cause.same_minute,
        ],
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


async def _attempt[Result](
    failed: set[str],
    name: str,
    read: Callable[[], Awaitable[Result]],
    fallback: Result,
    *,
    savepoint: AsyncConnection | None = None,
) -> Result:
    """One read on its own: a failure is remembered, never a crash and never a zero. A
    Postgres read runs in a savepoint, so its failure leaves the others able to run."""
    try:
        if savepoint is None:
            return await read()
        async with savepoint.begin_nested():
            return await read()
    except Exception as failure:
        logger.warning("evidence_read_failed", read=name, error=type(failure).__name__)
        failed.add(name)
        return fallback


async def _in_thread[Result](function: Callable[[], Result]) -> Result:
    """A ClickHouse read off the event loop: the driver blocks."""
    return await asyncio.to_thread(function)


async def read_facts(
    connection: AsyncConnection,
    store: EvidenceStore | None,
    row: reads.FindingRow,
    *,
    deployment: Deployment,
    now: datetime,
    cache: AsyncConnection,
) -> Facts:
    """Every read for one finding, each on its own (design, gather step 3). `connection`
    is in a transaction; `cache` is the lock's own connection, which commits the hour's
    error-group read."""
    failed: set[str] = set()
    truncated: set[str] = set()
    start = row.started_at
    end = min(row.closed_at or now, now)
    zone = _zone(row.timezone)
    service = row.service_name
    callers = [service] if service else list(row.services)
    telemetry: EvidenceReader | None = (
        None if store is None else store.for_project(row.org_id, row.project_id)
    )

    async def edges() -> Any:
        if telemetry is None:
            raise RuntimeError("no telemetry store configured")
        return await _in_thread(
            lambda: telemetry.edges(
                callers=callers, start=start, end=end, by_caller=service is None
            )
        )

    read_edges = await _attempt(failed, "dependencies", edges, None)
    edge_facts = () if read_edges is None else read_edges.edges
    if read_edges is not None and read_edges.truncated:
        truncated.add("dependencies")
    callees = [edge.callee for edge in edge_facts if edge.reported]
    if read_edges is not None and service is None:
        callees = sorted({edge.callee for _caller, edge in read_edges.by_caller if edge.reported})

    deploy_services = [*callers, *callees]
    deploys = await _attempt(
        failed,
        "deploys",
        lambda: reads.deploys(
            connection,
            org_id=row.org_id,
            project_id=row.project_id,
            services=deploy_services,
            seen_service=service,
            start=start,
        ),
        reads.Deploys((), {}, {}),
        savepoint=connection,
    )
    releases = None
    if service is not None:
        releases = await _attempt(
            failed,
            "releases",
            lambda: reads.releases(
                connection,
                org_id=row.org_id,
                project_id=row.project_id,
                service=service,
                start=start,
                callees=callees,
            ),
            None,
            savepoint=connection,
        )
    dependency_findings: list[rules.OtherFinding] = await _attempt(
        failed,
        "findings",
        lambda: reads.open_findings_of(
            connection,
            org_id=row.org_id,
            project_id=row.project_id,
            finding_id=row.id,
            services=callees,
        ),
        [],
        savepoint=connection,
    )
    incidents: list[rules.LinkedIncident] = await _attempt(
        failed,
        "incidents",
        lambda: reads.incidents(connection, org_id=row.org_id, finding_id=row.id),
        [],
        savepoint=connection,
    )
    checks: list[rules.CheckChange] = await _attempt(
        failed,
        "checks",
        lambda: reads.check_changes(
            connection, org_id=row.org_id, project_id=row.project_id, start=start, end=end
        ),
        [],
        savepoint=connection,
    )
    intake: list[datetime] = []
    if service is None and deployment == SELF_HOSTED:
        intake = await _attempt(
            failed,
            "intake",
            lambda: reads.intake_refusals(connection, start=start),
            [],
            savepoint=connection,
        )

    traffic = None
    if service is not None:

        async def read_traffic() -> Any:
            if telemetry is None:
                raise RuntimeError("no telemetry store configured")
            return await _in_thread(
                lambda: telemetry.traffic(
                    service=service,
                    lead_from=start - rules.TRAFFIC_LEAD_IN,
                    lead_to=start,
                    baseline=rules.TRAFFIC_BASELINE,
                )
            )

        traffic = await _attempt(failed, "traffic", read_traffic, None)

    async def read_errors() -> Any:
        if telemetry is None:
            raise RuntimeError("no telemetry store configured")
        return await _error_groups(cache, telemetry, row, start=start, now=now)

    no_groups: list[rules.ErrorGroup] = []
    groups: list[rules.ErrorGroup] = await _attempt(failed, "errors", read_errors, no_groups)

    async def read_hosts() -> Any:
        if telemetry is None:
            raise RuntimeError("no telemetry store configured")
        return await _in_thread(lambda: telemetry.hosts(start=start, end=end + _MINUTE))

    hosts = await _attempt(failed, "hosts", read_hosts, None)
    return Facts(
        finding=FindingInput(
            id=str(row.id),
            scope="project" if row.scope == "project" else "service",
            service=service,
            started_at=start,
            signals=tuple(s.get("signal") for s in row.signals if s.get("signal")),  # type: ignore[misc]
            services=tuple(row.services),
        ),
        now=now,
        zone=zone,
        deploys=deploys.in_window,
        histories=deploys.histories,
        seen_histories=deploys.seen_histories,
        coverage=None if releases is None else releases.coverage,
        edges=edge_facts if service is not None else (),
        project_edges=() if read_edges is None or service is not None else read_edges.by_caller,
        dependency_findings=tuple(dependency_findings),
        traffic=traffic,
        error_groups=tuple(groups_for(groups, callers)),
        checks=tuple(checks),
        incidents=tuple(incidents),
        hosts=() if hosts is None else hosts.readings,
        host_names_present=True if hosts is None else hosts.names_present,
        last_release=None if releases is None else releases.before,
        nearby_releases=() if releases is None else releases.near,
        intake_refused_at=tuple(intake),
        failed_reads=frozenset(failed),
        truncated_reads=frozenset(truncated),
    )


async def _error_groups(
    connection: AsyncConnection,
    telemetry: EvidenceReader,
    row: reads.FindingRow,
    *,
    start: datetime,
    now: datetime,
) -> list[rules.ErrorGroup]:
    """The project's new groups for this window, read once a minute per project (A62)."""
    window_from = (start - rules.ERROR_LEAD_IN).replace(second=0, microsecond=0)
    key = {"org_id": row.org_id, "project_id": row.project_id, "read_minute": window_from}
    cached = (
        await connection.execute(
            _CACHED_GROUPS, {**key, "fresh_from": now.replace(second=0, microsecond=0)}
        )
    ).scalar()
    if cached is not None:
        return [_group_from_json(group) for group in cached]
    groups = await _in_thread(
        lambda: telemetry.new_error_groups(window_from=window_from, window_to=now)
    )
    await connection.execute(
        _CACHE_GROUPS, {**key, "groups": json.dumps([_group_json(g) for g in groups])}
    )
    await connection.commit()
    return groups


def _group_json(group: rules.ErrorGroup) -> dict[str, Any]:
    return {
        "group_id": group.group_id,
        "exception_type": group.exception_type,
        "services": list(group.services),
        "count": group.count,
        "first_seen": group.first_seen.isoformat(),
    }


def _group_from_json(value: dict[str, Any]) -> rules.ErrorGroup:
    return rules.ErrorGroup(
        group_id=value["group_id"],
        exception_type=value["exception_type"],
        services=tuple(value["services"]),
        count=int(value["count"]),
        first_seen=datetime.fromisoformat(value["first_seen"]),
    )


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


# ------------------------------------------------------------------ the task

_store: EvidenceStore | None = None


def _the_store() -> EvidenceStore | None:
    """The reader, made once per child; None when no ClickHouse is configured, so every
    telemetry read is `could_not_read` rather than the job failing (AC1)."""
    global _store
    if _store is None:
        dsn = get_settings().clickhouse_dsn
        if dsn:
            _store = connect_store(str(dsn))
    return _store


@task(GATHER_EVIDENCE_TASK)
def gather_task(finding_id: str, org_id: str, reason: ReadReason) -> None:
    settings = get_settings()
    gathered = with_engine(
        lambda engine: gather(
            engine,
            _the_store(),
            finding_id=uuid.UUID(finding_id),
            org_id=uuid.UUID(org_id),
            reason=reason,
            deployment=settings.nexovue_deployment,
        )
    )
    if gathered is None:
        logger.info("evidence_finding_gone", finding_id=finding_id)
        return
    logger.info(
        "evidence_gathered",
        finding_id=finding_id,
        reason=reason,
        version=gathered.version,
        reads_failed=gathered.reads_failed,
        rule=gathered.likely_rule,
    )


__all__ = ["Gathered", "fingerprint", "gather", "gather_task", "read_facts"]
