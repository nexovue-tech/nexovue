"""Maintenance window logic, and the one question every reader asks of it:
which windows cover this check."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.checks.maintenance import WindowSchedule, is_active
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.checks import queries as check_queries
from nexovue_api.data.postgres.models import MaintenanceWindow
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.maintenance import queries
from nexovue_api.maintenance.schemas import (
    MAX_WINDOWS_PER_PROJECT,
    MaintenanceWindowListResponse,
    MaintenanceWindowSummary,
    WriteMaintenanceWindowRequest,
)
from nexovue_api.projects import service as projects

_PERIODS: Final = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def schedule_of(window: MaintenanceWindow) -> WindowSchedule:
    return WindowSchedule(
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        recurrence=window.recurrence,  # type: ignore[arg-type]  # CHECK constraint holds it
        repeat_until=window.repeat_until,
    )


async def schedules_by_check(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    check_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, list[MaintenanceWindow]]:
    """Every window covering each check, including project-wide ones."""
    windows = await queries.list_windows(session, org_id=org_id, project_id=project_id)
    covered = await queries.covered_checks(session, org_id=org_id, project_id=project_id)
    by_window: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for link in covered:
        by_window[link.window_id].add(link.check_id)

    result: dict[uuid.UUID, list[MaintenanceWindow]] = {}
    for check_id in check_ids:
        result[check_id] = [
            window
            for window in windows
            if window.applies_to_all or check_id in by_window.get(window.id, set())
        ]
    return result


async def list_windows(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> MaintenanceWindowListResponse:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    windows = await queries.list_windows(session, org_id=org_id, project_id=project_id)
    covered = await queries.covered_checks(session, org_id=org_id, project_id=project_id)
    by_window: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for link in covered:
        by_window[link.window_id].append(link.check_id)
    now = datetime.now(UTC)
    return MaintenanceWindowListResponse(
        windows=[_summary(window, by_window.get(window.id, []), now) for window in windows]
    )


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: WriteMaintenanceWindowRequest,
) -> MaintenanceWindowSummary:
    await projects.require_project(session, org_id=org_id, project_id=request.project_id)
    await _validate(session, org_id=org_id, request=request)
    existing = await queries.list_windows(session, org_id=org_id, project_id=request.project_id)
    if len(existing) >= MAX_WINDOWS_PER_PROJECT:
        raise ConflictError(
            f"A project may have at most {MAX_WINDOWS_PER_PROJECT} maintenance windows."
        )

    window = MaintenanceWindow(org_id=org_id, project_id=request.project_id)
    _apply(window, request)
    queries.add(session, window)
    await session.flush()
    await session.refresh(window)
    await queries.replace_covered_checks(
        session,
        org_id=org_id,
        project_id=request.project_id,
        window_id=window.id,
        check_ids=[] if request.applies_to_all else request.check_ids,
    )
    audit.record_maintenance_window_changed(
        session,
        org_id=org_id,
        window_id=window.id,
        actor_user_id=actor_user_id,
        action=audit.MAINTENANCE_WINDOW_CREATED,
    )
    return _summary(window, [] if request.applies_to_all else request.check_ids, datetime.now(UTC))


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    window_id: uuid.UUID,
    request: WriteMaintenanceWindowRequest,
) -> MaintenanceWindowSummary:
    window = await queries.find_window(session, org_id=org_id, window_id=window_id)
    if window is None or window.project_id != request.project_id:
        raise NotFoundError("No such maintenance window.")
    await _validate(session, org_id=org_id, request=request)
    _apply(window, request)
    check_ids = [] if request.applies_to_all else request.check_ids
    await queries.replace_covered_checks(
        session,
        org_id=org_id,
        project_id=window.project_id,
        window_id=window.id,
        check_ids=check_ids,
    )
    audit.record_maintenance_window_changed(
        session,
        org_id=org_id,
        window_id=window.id,
        actor_user_id=actor_user_id,
        action=audit.MAINTENANCE_WINDOW_UPDATED,
    )
    return _summary(window, check_ids, datetime.now(UTC))


async def remove(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, window_id: uuid.UUID
) -> None:
    if await queries.delete_window(session, org_id=org_id, window_id=window_id) is None:
        raise NotFoundError("No such maintenance window.")
    audit.record_maintenance_window_changed(
        session,
        org_id=org_id,
        window_id=window_id,
        actor_user_id=actor_user_id,
        action=audit.MAINTENANCE_WINDOW_DELETED,
    )


async def _validate(
    session: AsyncSession, *, org_id: uuid.UUID, request: WriteMaintenanceWindowRequest
) -> None:
    errors: list[dict[str, str]] = []
    if request.ends_at <= request.starts_at:
        errors.append({"field": "ends_at", "reason": "must be after the start"})
    period = _PERIODS.get(request.recurrence)
    if period is not None and request.ends_at - request.starts_at >= period:
        reason = f"a {request.recurrence} window must be shorter than its period"
        errors.append({"field": "ends_at", "reason": reason})
    if request.repeat_until is not None and request.recurrence == "none":
        errors.append({"field": "repeat_until", "reason": "only a repeating window repeats"})
    if request.applies_to_all and request.check_ids:
        errors.append({"field": "check_ids", "reason": "a project-wide window names no checks"})
    if not request.applies_to_all and not request.check_ids:
        errors.append({"field": "check_ids", "reason": "choose the checks, or the whole project"})

    if len(set(request.check_ids)) != len(request.check_ids):
        errors.append({"field": "check_ids", "reason": "a check is named more than once"})
    if request.check_ids:
        project_checks = await check_queries.list_checks(
            session, org_id=org_id, project_id=request.project_id
        )
        known = {check.id for check in project_checks}
        errors.extend(
            {"field": "check_ids", "reason": f"no check {check_id} in this project"}
            for check_id in request.check_ids
            if check_id not in known
        )
    if errors:
        raise InvalidRequestError("This maintenance window cannot be saved.", errors=errors)


def _apply(window: MaintenanceWindow, request: WriteMaintenanceWindowRequest) -> None:
    window.name = request.name
    window.starts_at = request.starts_at
    window.ends_at = request.ends_at
    window.recurrence = request.recurrence
    window.repeat_until = request.repeat_until
    window.applies_to_all = request.applies_to_all


def _summary(
    window: MaintenanceWindow, check_ids: list[uuid.UUID], now: datetime
) -> MaintenanceWindowSummary:
    return MaintenanceWindowSummary(
        id=window.id,
        project_id=window.project_id,
        name=window.name,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        recurrence=window.recurrence,
        repeat_until=window.repeat_until,
        applies_to_all=window.applies_to_all,
        check_ids=check_ids,
        active_now=is_active(schedule_of(window), now),
        created_at=window.created_at,
    )
