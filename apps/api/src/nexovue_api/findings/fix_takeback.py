"""Taking a suggestion's code back, at every end the API owns (spec BR9, AC29, AC29b; design,
*The sweep*, BR9's table; plan T5.3; ADR-094).

One way in each case, so no path forgets a column: work still being prepared ends first
(its pack goes with it, so the AI role's late write matches nothing under its row policy),
then the one statement, `clear_stored_code`, empties every column and table on the
deletion list. The record stays: the row, its state, reason, counts and times, its
verdict, and every audit entry.

- **An owner withdraws** (`fix_consent.withdraw`): the folders that consent alone covered.
- **The Fix App is disconnected** (F22's disconnect hook, in its transaction): every consent
  on the App's repositories is withdrawn, and all their stored code goes.
- **A project is deleted** (`projects.service.delete`, in its transaction): its suggestions
  of either kind still being prepared stop, and its stored code goes (ADR-094). Nothing
  more is sent for a project that is gone.
- **A member is removed** (`members.service.scrub`): their GitHub link and attempts, and
  their own check runs, whose patches are code, go.

Every statement binds the organization.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from nexovue_platform.fixes.records import (
    CODE_DISCONNECTED,
    CONSENT_ENDED_IN_FLIGHT,
    CONSENT_WITHDRAWN_BY_DISCONNECT,
    FAILED,
    IN_FLIGHT_STATES,
    KIND_SOURCE,
    PROJECT_DELETED,
)
from nexovue_platform.fixes.statements import CLEAR_STORED_CODE
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.connections.service import register_disconnect_hook
from nexovue_api.data.postgres.models import (
    ConnectionRepository,
    FindingFix,
    FindingFixCheck,
    FindingFixSourcePack,
    FixSourceConsent,
    GitHubLinkAttempt,
    GitHubUserLink,
)


async def end_and_clear(
    db: AsyncSession,
    rows: Sequence[FindingFix],
    *,
    in_flight_reason: str,
    code_reason: str,
    now: datetime,
) -> int:
    """End what is still being prepared, then delete every stored copy of code. Returns how
    many suggestions had code deleted. The rows must be locked by the caller."""
    for row in rows:
        if row.state in IN_FLIGHT_STATES:
            await _end_in_flight(db, row, reason=in_flight_reason, now=now)
    deleted = 0
    for row in rows:
        if row.kind != KIND_SOURCE or row.code_deleted_at is not None:
            continue
        if row.files_read is not None or row.edits is not None or row.state in IN_FLIGHT_STATES:
            deleted += int(
                await db.scalar(
                    text(CLEAR_STORED_CODE),
                    {"org_id": row.org_id, "fix_id": row.id, "reason": code_reason},
                )
                or 0
            )
    return deleted


async def _end_in_flight(db: AsyncSession, row: FindingFix, *, reason: str, now: datetime) -> None:
    await db.execute(
        update(FindingFix)
        .where(FindingFix.org_id == row.org_id, FindingFix.id == row.id)
        .values(
            state=FAILED,
            reason=reason,
            state_changed_at=now,
            finished_at=now,
            path_candidates=None,
            requested_paths=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(FindingFixSourcePack).where(
            FindingFixSourcePack.org_id == row.org_id, FindingFixSourcePack.fix_id == row.id
        )
    )


async def on_disconnect(db: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    """F22's hook, inside its disconnect's transaction (design, *What other features must
    carry*): a disconnect withdraws every consent on the App's repositories, so this one
    table keeps answering "may source go?", and every stored copy of code goes. A tracker
    connection reaches no consent, so it changes nothing here."""
    now = datetime.now(UTC)
    repositories = select(ConnectionRepository.id).where(
        ConnectionRepository.org_id == org_id, ConnectionRepository.connection_id == connection_id
    )
    consents = list(
        await db.scalars(
            select(FixSourceConsent)
            .where(
                FixSourceConsent.org_id == org_id,
                FixSourceConsent.connection_repository_id.in_(repositories),
                FixSourceConsent.withdrawn_at.is_(None),
            )
            .with_for_update()
        )
    )
    rows = list(
        await db.scalars(
            select(FindingFix)
            .where(
                FindingFix.org_id == org_id,
                FindingFix.kind == KIND_SOURCE,
                FindingFix.connection_repository_id.in_(repositories),
                FindingFix.code_deleted_at.is_(None),
            )
            .with_for_update()
        )
    )
    deleted = await end_and_clear(
        db,
        rows,
        in_flight_reason=CONSENT_ENDED_IN_FLIGHT,
        code_reason=CODE_DISCONNECTED,
        now=now,
    )
    for consent in consents:
        consent.withdrawn_at = now
        consent.withdrawn_reason = CONSENT_WITHDRAWN_BY_DISCONNECT
        audit.record_fix_event(
            db,
            org_id=org_id,
            actor_user_id=None,
            action=audit.AI_SOURCE_DISCLOSURE_WITHDRAWN,
            target_kind=audit.REPOSITORY_TARGET,
            target_id=consent.connection_repository_id,
            detail={
                "consent_id": str(consent.id),
                "folder_scope": consent.folder_scope,
                "reason": CONSENT_WITHDRAWN_BY_DISCONNECT,
                "code_deleted": deleted,
            },
        )


async def end_for_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, now: datetime
) -> int:
    """A deleted project's suggestions: those still being prepared stop, of either kind,
    and every stored copy of code goes (ADR-094). Returns how many had code deleted."""
    rows = list(
        await db.scalars(
            select(FindingFix)
            .where(
                FindingFix.org_id == org_id,
                FindingFix.project_id == project_id,
                FindingFix.code_deleted_at.is_(None) | FindingFix.state.in_(IN_FLIGHT_STATES),
            )
            .with_for_update()
        )
    )
    return await end_and_clear(
        db, rows, in_flight_reason=PROJECT_DELETED, code_reason=PROJECT_DELETED, now=now
    )


async def forget_person(db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """At member removal (AC31): their GitHub identity, and their own check runs, which hold
    their GitHub access answer and their patches. Audit entries name only their id."""
    await db.execute(
        delete(GitHubUserLink).where(
            GitHubUserLink.org_id == org_id, GitHubUserLink.user_id == user_id
        )
    )
    await db.execute(
        delete(GitHubLinkAttempt).where(
            GitHubLinkAttempt.org_id == org_id, GitHubLinkAttempt.user_id == user_id
        )
    )
    await db.execute(
        delete(FindingFixCheck).where(
            FindingFixCheck.org_id == org_id, FindingFixCheck.requested_by == user_id
        )
    )


# F22 calls each registered hook inside its disconnect's transaction ("F5 registers one for
# its App", `connections/service.py`); imported with the API, before any request.
register_disconnect_hook(on_disconnect)
