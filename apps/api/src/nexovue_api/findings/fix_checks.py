"""`POST /findings/{id}/fixes/{fix_id}/check` for `read`: **Check my access** (spec D20,
AC10a's read half; design, *The check run*; plan T4.12). T5.1 adds `patch`.

One check row per person and purpose. A repeat click while one runs returns the running
check; a click within 60 seconds of a finished one returns that one (a guess), since each
costs GitHub reads on a shared queue; otherwise the finished row is replaced and a job is
added, with ids only, in the click's transaction. The section polls the row through the
suggestion's own read. **Nothing here calls GitHub:** `scheduler-connections` does.

Offered only where it means something: a written suggestion, under the owner's requirement,
to a person with a linked GitHub account (otherwise the link step comes first).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.records import (
    CHECK_FOR_READ,
    CHECK_REUSED_WITHIN,
    CHECK_ROW_LIFETIME,
    CHECK_RUNNING,
    KIND_SOURCE,
    PROPOSED,
    check_fix_request,
)
from nexovue_platform.fixes.sentences import GENERAL_HAS_NO_CODE, LINK_SENTENCE
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import (
    FindingFix,
    FindingFixCheck,
    FixSourceConsent,
    GitHubUserLink,
)
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.findings import fix_queries
from nexovue_api.findings.fix_proposal import check_view
from nexovue_api.findings.fix_source_schemas import CheckView

NO_SUCH_FIX: Final = "No such suggestion for this finding."
NOT_PROPOSED: Final = "There is no written suggestion to check access for."
NOT_REQUIRED: Final = "The owner does not limit quoted code here, so there is nothing to check."
NOT_LINKED_CODE: Final = "not_linked"
GENERAL_HAS_NO_CODE_CODE: Final = "general_has_no_code"


async def ask_check(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    now: datetime,
) -> CheckView:
    org_id, user_id = identity.org_id, identity.user.id
    row = await fix_queries.fix_of(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row is None:
        raise NotFoundError(NO_SUCH_FIX)
    if row.kind != KIND_SOURCE:
        raise ConflictError(GENERAL_HAS_NO_CODE, code=GENERAL_HAS_NO_CODE_CODE)
    if row.state != PROPOSED or row.code_deleted_at is not None:
        raise ConflictError(NOT_PROPOSED, code="not_proposed")
    if not await _owner_requires_access(db, row):
        raise ConflictError(NOT_REQUIRED, code="not_required")
    linked = await db.scalar(
        select(GitHubUserLink.user_id).where(
            GitHubUserLink.org_id == org_id, GitHubUserLink.user_id == user_id
        )
    )
    if linked is None:
        raise ConflictError(LINK_SENTENCE, code=NOT_LINKED_CODE)
    existing: FindingFixCheck | None = await db.scalar(
        select(FindingFixCheck)
        .where(
            FindingFixCheck.org_id == org_id,
            FindingFixCheck.fix_id == fix_id,
            FindingFixCheck.requested_by == user_id,
            FindingFixCheck.purpose == CHECK_FOR_READ,
        )
        .with_for_update()
    )
    if existing is not None and _still_useful(existing, now=now):
        return check_view(existing, now=now)
    if existing is not None:
        await db.execute(
            delete(FindingFixCheck).where(
                FindingFixCheck.org_id == org_id, FindingFixCheck.id == existing.id
            )
        )
    check = FindingFixCheck(
        id=uuid.uuid4(),
        org_id=org_id,
        fix_id=fix_id,
        requested_by=user_id,
        purpose=CHECK_FOR_READ,
        status=CHECK_RUNNING,
        requested_at=now,
        expires_at=now + CHECK_ROW_LIFETIME,
    )
    db.add(check)
    await db.flush()
    await fix_queries.queue_suggestion(db, check_fix_request(check_id=check.id, org_id=org_id))
    await db.commit()
    return check_view(check, now=now)


def _still_useful(check: FindingFixCheck, *, now: datetime) -> bool:
    """A running check, or one finished in the last minute, is returned, not repeated."""
    if check.status == CHECK_RUNNING:
        return check.expires_at > now
    return check.finished_at is not None and check.finished_at + CHECK_REUSED_WITHIN > now


async def _owner_requires_access(db: AsyncSession, row: FindingFix) -> bool:
    if row.connection_repository_id is None:
        return False
    consents = await db.scalars(
        select(FixSourceConsent).where(
            FixSourceConsent.org_id == row.org_id,
            FixSourceConsent.connection_repository_id == row.connection_repository_id,
            FixSourceConsent.withdrawn_at.is_(None),
        )
    )
    return any(
        each.require_github_access and folder_covered(each.folder_scope, row.folder)
        for each in consents
    )
