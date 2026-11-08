"""Reliability targets: list, read, create, change, delete and restore (F19).

Four kinds (FR1), each with its own fields: a request kind names its service, an
endpoint target its endpoint, a latency target its typed threshold, snapped down to a
boundary (FR3), and a check target its check. Another kind's field is refused beside
it, and a source is checked against the project at write (design, *Tenancy path*).

The list reads Postgres alone: each target's row, its status and its stored days, so
it answers with ClickHouse down (AC40, NFR3). Its state is the platform's one function
(`nexovue_platform.slo.status_of`), so a screen and a message cannot disagree.

Every write records a row of the target's history (FR30) and an audit entry (AC34),
both in the action's own transaction, with ids only in the audit detail (AC88).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from nexovue_platform.detection import DEFAULT_TIMEZONE
from nexovue_platform.jobs import ENQUEUE, WAKE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity, Reader
from nexovue_api.data.postgres.models import Project, SloDailyCount, SloStatus, SloTarget
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.projects.service import require_project, require_readable_project
from nexovue_api.slos import queries
from nexovue_api.slos.schemas import (
    DeletedSlo,
    SloChange,
    SloCreate,
    SloFigures,
    SloList,
    SloRestore,
    SloView,
)

NO_SUCH_TARGET: Final = "No such reliability target"
TOO_MANY: Final = (
    f"A project holds at most {slo.TARGETS_PER_PROJECT} targets. The limit protects the "
    "system, not your plan: delete one you no longer read to add another."
)
NAME_TAKEN: Final = "Another target in this project already has this name."
NO_SUCH_SERVICE: Final = "This project has never heard from a service by that name."
NAME_THE_SERVICE: Final = "Name the service to count."
NAME_THE_ENDPOINT: Final = "Name the endpoint to count."
GIVE_A_THRESHOLD: Final = "Give the response time a request must be at or under, in ms."
CHOOSE_A_CHECK: Final = "Choose the check to count."
NO_SUCH_CHECK: Final = "This project has no such check."
ANSWER_SAMPLING: Final = "Say whether you sample before sending to nexovue."
NOT_FOR_THIS_KIND: Final = "This kind of target does not take this."
LEFT_OUT_TWICE: Final = "Each endpoint is left out once."
SAME_DEFINITION: Final = (
    "A target with this definition already exists. Two would read the same in a message."
)
# What a change records and compares: the definition and the words, never a person.
_RECORDED: Final = (
    "name",
    "description",
    "service_name",
    "endpoint_name",
    "check_id",
    "boundary_index",
    "asked_ms",
    "left_out_endpoints",
    "objective_percent",
    "window_days",
    "upstream_sampling",
    "enabled",
)
# What is counted changed: the past must be counted again (FR28). The objective and the
# window change the figure, not the counts, so they need no recount.
_COUNTED: Final = frozenset(
    {"service_name", "endpoint_name", "check_id", "boundary_index", "left_out_endpoints"}
)
# The fields only some kinds take (design, *Data*), and which kinds.
_FIELD_KINDS: Final = {
    "service_name": slo.REQUEST_KINDS,
    "upstream_sampling": slo.REQUEST_KINDS,
    "endpoint_name": (slo.ENDPOINT_AVAILABILITY,),
    "asked_ms": (slo.SERVICE_LATENCY,),
    "left_out_endpoints": (slo.SERVICE_AVAILABILITY,),
    "check_id": (slo.CHECK_AVAILABILITY,),
}


# ------------------------------------------------------------------------ reads


async def list_targets(
    db: AsyncSession, *, reader: Reader, project_id: uuid.UUID, now: datetime
) -> SloList:
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    zone = zone_of(project)
    rows = await queries.targets_with_status(db, org_id=reader.org_id, project_id=project_id)
    live = [row for row in rows if row[0].deleted_at is None]
    recent = [
        row for row in rows if row[0].deleted_at and now - row[0].deleted_at < slo.RESTORE_FOR
    ]
    days = await _days(db, reader.org_id, [row[0] for row in [*live, *recent]], now, zone)
    views = [_view(row, days.get(row[0].id, {}), now=now, zone=zone) for row in live]
    ordered = slo.ordered([(view, _status_of(view)) for view in views])
    return SloList(
        targets=[view for view, _ in ordered],
        deleted=[_deleted_line(row, days.get(row[0].id, {}), reader=reader) for row in recent],
        zone=str(zone),
        has_services=await queries.has_services(db, org_id=reader.org_id, project_id=project_id),
    )


async def read_target(
    db: AsyncSession, *, reader: Reader, target_id: uuid.UUID, now: datetime
) -> SloView:
    """One live target. It takes no project, so the caller's scope is compared against
    the row it loads, as `get_incident` does (AC33)."""
    row = await queries.target_with_status(db, org_id=reader.org_id, target_id=target_id)
    if row is None or row[0].deleted_at is not None:
        raise NotFoundError(NO_SUCH_TARGET)
    target = row[0]
    project = await require_readable_project(db, reader=reader, project_id=target.project_id)
    zone = zone_of(project)
    days = await _days(db, reader.org_id, [target], now, zone)
    return _view(row, days.get(target.id, {}), now=now, zone=zone)


# ----------------------------------------------------------------------- writes


async def create(
    db: AsyncSession,
    *,
    identity: Identity,
    project_id: uuid.UUID,
    request: SloCreate,
    now: datetime,
) -> SloView:
    org_id = identity.organization.id
    await require_project(db, org_id=org_id, project_id=project_id)
    _refuse_objective(request.objective_percent)
    sent = request.model_dump(exclude_unset=True)
    _refuse_other_kinds_fields(request.kind, sent)
    source = await _source(
        db,
        org_id,
        project_id,
        request.kind,
        service_name=request.service_name,
        endpoint_name=request.endpoint_name,
        check_id=request.check_id,
        asked_ms=request.asked_ms,
        left_out_endpoints=request.left_out_endpoints,
        upstream_sampling=request.upstream_sampling,
    )
    if await queries.live_count(db, org_id=org_id, project_id=project_id) >= (
        slo.TARGETS_PER_PROJECT
    ):
        raise ConflictError(TOO_MANY)
    key = _definition_key(request.kind, source, request.objective_percent, request.window_days)
    await _refuse_taken(db, org_id, project_id, name=request.name, definition_key=key)
    target = SloTarget(
        org_id=org_id,
        project_id=project_id,
        name=request.name,
        description=request.description,
        kind=request.kind,
        service_name=source.service_name,
        endpoint_name=source.endpoint_name,
        check_id=source.check_id,
        boundary_index=source.boundary_index,
        asked_ms=source.asked_ms,
        left_out_endpoints=list(source.left_out_endpoints),
        objective_percent=request.objective_percent,
        window_days=request.window_days,
        upstream_sampling=source.upstream_sampling,
        # FR5: a customer who samples first gets false pages, so alerts start off.
        enabled=source.upstream_sampling != "yes",
        definition_key=key,
        created_by=identity.user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(target)
    await db.flush()
    queries.record_change(
        db,
        target=target,
        actor_user_id=identity.user.id,
        what="created",
        before=None,
        after=_recorded(target),
        at=now,
    )
    audit.record_slo_created(db, target=target, actor_user_id=identity.user.id)
    await _queue_history(db, target)
    await db.commit()
    return await _fresh_view(db, identity, target.id, now)


async def change(
    db: AsyncSession,
    *,
    identity: Identity,
    target_id: uuid.UUID,
    request: SloChange,
    now: datetime,
) -> SloView:
    org_id = identity.organization.id
    target = await _live_for_change(db, org_id, target_id)
    sent = request.model_dump(exclude_unset=True, exclude_none=True)
    if "objective_percent" in sent:
        _refuse_objective(sent["objective_percent"])
    _refuse_other_kinds_fields(target.kind, sent)
    source = await _source(
        db,
        org_id,
        target.project_id,
        target.kind,
        service_name=sent.get("service_name", target.service_name),
        endpoint_name=sent.get("endpoint_name", target.endpoint_name),
        check_id=sent.get("check_id", target.check_id),
        asked_ms=sent.get("asked_ms", target.asked_ms),
        left_out_endpoints=sent.get("left_out_endpoints", target.left_out_endpoints),
        upstream_sampling=sent.get("upstream_sampling", target.upstream_sampling),
        known_service=target.service_name,
        known_check=target.check_id,
    )
    before = _recorded(target)
    for field, value in sent.items():
        setattr(target, field, value)
    target.boundary_index = source.boundary_index
    target.left_out_endpoints = list(source.left_out_endpoints)
    key = _definition_key(target.kind, source, target.objective_percent, target.window_days)
    await _refuse_taken(
        db,
        org_id,
        target.project_id,
        name=target.name if "name" in sent else None,
        definition_key=key if key != target.definition_key else None,
        other_than=target.id,
    )
    target.definition_key = key
    after_change = _recorded(target)
    recount = any(before[field] != after_change[field] for field in _COUNTED)
    if recount:
        target.definition_version += 1
    target.updated_at = now
    after = _recorded(target)
    if after != before:
        queries.record_change(
            db,
            target=target,
            actor_user_id=identity.user.id,
            what="changed",
            before=before,
            after=after,
            at=now,
        )
        audit.record_slo_target_changed(db, target=target, actor_user_id=identity.user.id)
    if recount:
        await _queue_history(db, target)
    await db.commit()
    return await _fresh_view(db, identity, target.id, now)


async def delete(
    db: AsyncSession, *, identity: Identity, target_id: uuid.UUID, now: datetime
) -> None:
    """A soft delete (FR31): off the list, restorable for 30 days, its name free again."""
    org_id = identity.organization.id
    target = await _live_for_change(db, org_id, target_id)
    target.deleted_at = now
    target.deleted_by = identity.user.id
    target.updated_at = now
    queries.record_change(
        db,
        target=target,
        actor_user_id=identity.user.id,
        what="deleted",
        before=None,
        after=None,
        at=now,
    )
    audit.record_slo_deleted(db, target=target, actor_user_id=identity.user.id)
    await db.commit()


async def restore(
    db: AsyncSession,
    *,
    identity: Identity,
    target_id: uuid.UUID,
    request: SloRestore,
    now: datetime,
) -> SloView:
    """Back on the list, under a new name if its own was taken meanwhile (AC116), and
    counted again for the gap the source still holds (FR31)."""
    org_id = identity.organization.id
    target = await queries.target_for_change(db, org_id=org_id, target_id=target_id)
    if target is None or target.deleted_at is None or now - target.deleted_at >= slo.RESTORE_FOR:
        raise NotFoundError(NO_SUCH_TARGET)
    name = request.name or target.name
    await _refuse_taken(
        db, org_id, target.project_id, name=name, definition_key=target.definition_key
    )
    target.name = name
    target.deleted_at = None
    target.deleted_by = None
    target.updated_at = now
    # The gap is read again under the same definition, so the days counted before the
    # deletion keep their version and never read as an earlier definition (AC45).
    target.history_read_version = min(target.history_read_version, target.definition_version - 1)
    queries.record_change(
        db,
        target=target,
        actor_user_id=identity.user.id,
        what="restored",
        before=None,
        after={"name": name},
        at=now,
    )
    audit.record_slo_restored(db, target=target, actor_user_id=identity.user.id)
    await _queue_history(db, target)
    await db.commit()
    return await _fresh_view(db, identity, target.id, now)


# ---------------------------------------------------------------------- helpers


def zone_of(project: Project) -> ZoneInfo:
    return ZoneInfo(project.timezone or DEFAULT_TIMEZONE)


@dataclass(frozen=True, slots=True)
class _Source:
    """What a target counts, checked against its kind and its project."""

    service_name: str | None = None
    endpoint_name: str | None = None
    check_id: uuid.UUID | None = None
    boundary_index: int | None = None
    asked_ms: float | None = None
    left_out_endpoints: tuple[str, ...] = ()
    upstream_sampling: str | None = None


def _definition_key(kind: str, source: _Source, objective: Decimal, window: int) -> str:
    return slo.definition_key(
        kind,
        service_name=source.service_name,
        endpoint_name=source.endpoint_name,
        check_id=source.check_id,
        boundary_index=source.boundary_index,
        objective=objective,
        window_days=window,
    )


def _refuse_other_kinds_fields(kind: str, sent: Mapping[str, Any]) -> None:
    """A field another kind takes is refused beside it, so an endpoint never reaches a
    latency target (AC4) and a check target is never asked about sampling."""
    for field, kinds in _FIELD_KINDS.items():
        value = sent.get(field)
        if value not in (None, [], ()) and kind not in kinds:
            raise InvalidRequestError(NOT_FOR_THIS_KIND, field=field)


async def _source(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    *,
    service_name: str | None,
    endpoint_name: str | None,
    check_id: uuid.UUID | None,
    asked_ms: float | None,
    left_out_endpoints: Sequence[str],
    upstream_sampling: str | None,
    known_service: str | None = None,
    known_check: uuid.UUID | None = None,
) -> _Source:
    """The kind's own fields, each required where the kind needs it (FR1, FR3, FR5,
    FR34). A source is checked against the project unless it is the one already held:
    a change of name alone does not re-ask, and a deleted check leaves its target
    pointing at nothing, to read Source gone (AC70)."""
    if kind == slo.CHECK_AVAILABILITY:
        if check_id is None:
            raise InvalidRequestError(CHOOSE_A_CHECK, field="check_id")
        if check_id != known_check and not await queries.check_in_project(
            db, org_id=org_id, project_id=project_id, check_id=check_id
        ):
            raise InvalidRequestError(NO_SUCH_CHECK, field="check_id")
        return _Source(check_id=check_id)
    if not service_name:
        raise InvalidRequestError(NAME_THE_SERVICE, field="service_name")
    if service_name != known_service:
        await _refuse_unknown_service(db, org_id, project_id, service_name)
    if upstream_sampling is None:
        raise InvalidRequestError(ANSWER_SAMPLING, field="upstream_sampling")
    source = _Source(service_name=service_name, upstream_sampling=upstream_sampling)
    if kind == slo.ENDPOINT_AVAILABILITY:
        if not endpoint_name:
            raise InvalidRequestError(NAME_THE_ENDPOINT, field="endpoint_name")
        return replace(source, endpoint_name=endpoint_name)
    if kind == slo.SERVICE_LATENCY:
        return replace(source, asked_ms=asked_ms, boundary_index=_boundary(asked_ms))
    if len(set(left_out_endpoints)) != len(left_out_endpoints):
        raise InvalidRequestError(LEFT_OUT_TWICE, field="left_out_endpoints")
    return replace(source, left_out_endpoints=tuple(left_out_endpoints))


def _boundary(asked_ms: float | None) -> int:
    """FR3: the boundary at or under the typed threshold; outside the boundaries it is
    refused beside the field (AC101)."""
    if asked_ms is None:
        raise InvalidRequestError(GIVE_A_THRESHOLD, field="asked_ms")
    reason = slo.latency_refusal(asked_ms)
    index = slo.boundary_at_or_under(asked_ms)
    if reason is not None or index is None:
        raise InvalidRequestError(reason or GIVE_A_THRESHOLD, field="asked_ms")
    return index


def _refuse_objective(objective: Decimal) -> None:
    reason = slo.objective_refusal(objective)
    if reason is not None:
        raise InvalidRequestError(reason, field="objective_percent")


async def _refuse_unknown_service(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> None:
    """A target's source is checked against its project at write (design, *Tenancy
    path*): a name the project never reported would read nothing, silently."""
    if not await queries.has_reported(
        db, org_id=org_id, project_id=project_id, service_name=service_name
    ):
        raise InvalidRequestError(NO_SUCH_SERVICE, field="service_name")


async def _refuse_taken(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    name: str | None,
    definition_key: str | None,
    other_than: uuid.UUID | None = None,
) -> None:
    if name is not None and await queries.live_holding(
        db, org_id=org_id, project_id=project_id, name=name, other_than=other_than
    ):
        raise ConflictError(NAME_TAKEN, field="name")
    if definition_key is not None:
        holder = await queries.live_holding(
            db,
            org_id=org_id,
            project_id=project_id,
            definition_key=definition_key,
            other_than=other_than,
        )
        if holder is not None:
            raise ConflictError(SAME_DEFINITION, field="definition", existing_id=str(holder))


async def _live_for_change(db: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID) -> SloTarget:
    target = await queries.target_for_change(db, org_id=org_id, target_id=target_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError(NO_SUCH_TARGET)
    return target


async def _queue_history(db: AsyncSession, target: SloTarget) -> None:
    """In the action's transaction, so the read is queued exactly when the change lands.
    The scheduler's hand-out also picks up any target still behind (AC64)."""
    request = slo.history_request(target.org_id, target.id)
    await db.execute(text(ENQUEUE), request.parameters())
    await db.execute(text(WAKE), request.wake_parameters())


async def _fresh_view(
    db: AsyncSession, identity: Identity, target_id: uuid.UUID, now: datetime
) -> SloView:
    reader = Reader(org_id=identity.organization.id, identity=identity)
    return await read_target(db, reader=reader, target_id=target_id, now=now)


def _recorded(target: SloTarget) -> dict[str, Any]:
    """The definition as FR30's history keeps it: JSON values, no person."""
    values: dict[str, Any] = {}
    for field in _RECORDED:
        value = getattr(target, field)
        if isinstance(value, Decimal | uuid.UUID):
            value = str(value)
        elif isinstance(value, list):
            value = list(value)
        values[field] = value
    return values


async def _days(
    db: AsyncSession,
    org_id: uuid.UUID,
    targets: Sequence[SloTarget],
    now: datetime,
    zone: ZoneInfo,
) -> dict[uuid.UUID, dict[date, slo.DayCounts]]:
    since = now.astimezone(zone).date() - timedelta(days=max(slo.WINDOWS_IN_DAYS) - 1)
    rows = await queries.days_since(
        db, org_id=org_id, target_ids=[target.id for target in targets], since=since
    )
    found: dict[uuid.UUID, dict[date, slo.DayCounts]] = {}
    for row in rows:
        found.setdefault(row.slo_id, {})[row.day] = day_counts(row)
    return found


def day_counts(row: SloDailyCount) -> slo.DayCounts:
    """A stored day as the platform's arithmetic reads it, for a window or a month."""
    return slo.DayCounts.of_row(row)


def label_of(target: SloTarget, check_name: str | None) -> str:
    return slo.subject_label(
        target.kind,
        service_name=target.service_name,
        endpoint_name=target.endpoint_name,
        check_name=check_name or slo.DELETED_CHECK_NAME,
        boundary_index=target.boundary_index,
        objective=target.objective_percent,
        window_days=target.window_days,
    )


def _view(
    row: Sequence[Any],
    days: Mapping[date, slo.DayCounts],
    *,
    now: datetime,
    zone: ZoneInfo,
) -> SloView:
    """One target from a row of (target, status, deleted-by name, check name, interval)."""
    target: SloTarget = row[0]
    status: SloStatus | None = row[1]
    check_name: str | None = row[3]
    check_interval: int | None = row[4]
    stored = slo.StoredTarget(
        kind=target.kind,
        objective=target.objective_percent,
        window_days=target.window_days,
        enabled=target.enabled,
        first_event_at=target.first_event_at,
        definition_version=target.definition_version,
        history_read_version=target.history_read_version,
        source_missing=target.kind == slo.CHECK_AVAILABILITY and target.check_id is None,
        evaluated_minute=status.evaluated_minute if status else None,
        last_event_minute=status.last_event_minute if status else None,
        longest_quiet_7d_minutes=status.longest_quiet_7d if status else 0,
        created_at=target.created_at,
    )
    facts, figures = slo.facts_from_storage(
        stored, days, now=now, zone=zone, check_interval_seconds=check_interval
    )
    state = slo.status_of(facts)
    left = slo.allowance_left(target.objective_percent, figures.total, figures.failures)
    return SloView(
        id=target.id,
        project_id=target.project_id,
        name=target.name,
        description=target.description,
        kind=target.kind,
        service_name=target.service_name,
        endpoint_name=target.endpoint_name,
        check_id=target.check_id,
        check_name=check_name,
        check_interval_seconds=check_interval,
        boundary_index=target.boundary_index,
        boundary_ms=(
            None if target.boundary_index is None else slo.boundary_ms(target.boundary_index)
        ),
        asked_ms=target.asked_ms,
        left_out_endpoints=list(target.left_out_endpoints),
        objective_percent=target.objective_percent,
        window_days=target.window_days,
        upstream_sampling=target.upstream_sampling,
        enabled=target.enabled,
        label=label_of(target, check_name),
        allowance_words=slo.allowance_words(target.kind),
        state=state.state,
        variant=state.variant,
        qualifiers=list(state.qualifiers),
        attention=state.attention,
        severity=state.severity,
        hours_left=state.hours_left,
        quiet_minutes=state.quiet_minutes,
        figures=SloFigures(
            good=figures.good,
            total=figures.total,
            failures=figures.failures,
            allowed=facts.allowed,
            left=slo.shown_allowance(left),
            missed_by=max(0, -left),
            figure_percent=slo.figure_percent(figures.good, figures.total),
            coverage=figures.coverage,
            lost_minutes=figures.lost_minutes,
            excluded_minutes=figures.excluded_minutes,
            days_held=figures.days_held,
        ),
        evaluated_minute=stored.evaluated_minute,
        last_event_minute=stored.last_event_minute,
        longest_quiet_7d_minutes=stored.longest_quiet_7d_minutes,
        zone=str(zone),
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _status_of(view: SloView) -> slo.Status:
    return slo.Status(
        state=view.state,
        variant=view.variant,
        qualifiers=tuple(view.qualifiers),
        attention=view.attention,
        severity=view.severity,
        hours_left=view.hours_left,
        quiet_minutes=view.quiet_minutes,
    )


def _deleted_line(
    row: Sequence[Any],
    days: Mapping[date, slo.DayCounts],
    *,
    reader: Reader,
) -> DeletedSlo:
    target: SloTarget = row[0]
    deleted_by_name: str | None = row[2]
    assert target.deleted_at is not None
    return DeletedSlo(
        id=target.id,
        name=target.name,
        label=label_of(target, row[3]),
        deleted_at=target.deleted_at,
        purged_on=(target.deleted_at + slo.RESTORE_FOR).date(),
        days_held=len(days),
        deleted_by_name=None if reader.is_token else deleted_by_name,
    )
