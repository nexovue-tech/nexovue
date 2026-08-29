"""Check logic: what may be created, and who may see it.

Two gates, and neither is optional.

**The project must belong to the caller.** `project_id` arrives in a request
body, which makes it a value the caller chose. It becomes a row's tenancy only
after `projects.require_project` has proved it is theirs.

**The target must be one we will fetch.** A check points our own infrastructure
at a URL somebody else picked; `targets.validate_target` says which of those we
agree to make.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.checks import queries, targets
from nexovue_api.checks.schemas import (
    CheckListResponse,
    CheckSummary,
    CreateCheckRequest,
    UpdateCheckRequest,
)
from nexovue_api.data.postgres.models import Check
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.projects import service as projects


async def list_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> CheckListResponse:
    """Every check on one project, after proving the project is the caller's."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_checks(session, org_id=org_id, project_id=project_id)
    return CheckListResponse(checks=[_summary(row) for row in rows])


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: CreateCheckRequest,
) -> CheckSummary:
    """Define a new check.

    The audit record joins this transaction, so the check and the record of who
    created it commit together or neither does.
    """
    project = await projects.require_project(session, org_id=org_id, project_id=request.project_id)

    check = Check(
        org_id=org_id,
        project_id=project.id,
        type=request.type,
        target=targets.validate_target(request.target),
        interval_seconds=request.interval_seconds,
        config=request.config,
        enabled=True,
    )
    queries.add_check(session, check)
    # Needed before the audit record and the response, both of which name the
    # row by the id Postgres assigns.
    await session.flush()

    audit.record_check_created(session, check=check, actor_user_id=actor_user_id)
    return _summary(check)


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    check_id: uuid.UUID,
    request: UpdateCheckRequest,
) -> CheckSummary:
    """Edit a check in place. Omitted fields are left alone."""
    check = await _require_check(session, org_id=org_id, check_id=check_id)

    if request.target is not None:
        # Re-validated on every edit. A target that was public when the check
        # was created is not evidence about the one replacing it.
        check.target = targets.validate_target(request.target)
    if request.interval_seconds is not None:
        check.interval_seconds = request.interval_seconds
    if request.enabled is not None:
        check.enabled = request.enabled
    if request.config is not None:
        check.config = request.config

    audit.record_check_updated(session, check=check, actor_user_id=actor_user_id)
    return _summary(check)


async def remove(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    check_id: uuid.UUID,
) -> None:
    """Delete a check, or 404 if it is not this organization's."""
    if not await queries.delete_check(session, org_id=org_id, check_id=check_id):
        raise NotFoundError("No such check.")
    audit.record_check_deleted(
        session, org_id=org_id, check_id=check_id, actor_user_id=actor_user_id
    )


async def _require_check(session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID) -> Check:
    """The check, or a 404 that does not say whether it exists elsewhere."""
    check = await queries.find_check(session, org_id=org_id, check_id=check_id)
    if check is None:
        raise NotFoundError("No such check.")
    return check


def _summary(check: Check) -> CheckSummary:
    return CheckSummary(
        id=check.id,
        project_id=check.project_id,
        # The column is free text; the schema narrows it to the types we run.
        type=check.type,
        target=check.target,
        interval_seconds=check.interval_seconds,
        enabled=check.enabled,
        config=check.config,
        created_at=check.created_at,
    )
