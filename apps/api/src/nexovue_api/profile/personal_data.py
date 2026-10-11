"""A person's own data: downloading it, and deleting their account. No HTTP.

`docs/features/personal-data/spec.md`. The export holds what nexovue holds about
the person *as a person* — never another person's data, never the organization's
telemetry. Deleting an account is the member removal that already exists
(`members.service.scrub`), started by the person instead of an admin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import second_factor
from nexovue_api.auth.credentials import CURRENT_PASSWORD_WRONG, verify_password
from nexovue_api.auth.encryption import CredentialBox
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
from nexovue_api.members import queries as members_queries
from nexovue_api.members import service as members
from nexovue_api.profile import queries
from nexovue_api.profile.schemas import (
    ExportedActivity,
    ExportedApiToken,
    ExportedInvitationReceived,
    ExportedInvitationSent,
    ExportedOrganization,
    ExportedProfile,
    ExportedSession,
    PersonalDataExport,
)

# Years of activity still answers in seconds at this bound (spec, NFR).
MAX_ACTIVITY_ENTRIES: Final = 10_000

SOLE_OWNER_REFUSAL: Final = (
    "You are this organization's only owner. "
    "Make someone else an owner, or delete the organization."
)
CODE_NEEDED: Final = "Enter the code from your authenticator app, or a recovery code."
CODE_WRONG: Final = "That code was not accepted. Check your authenticator and try again."


async def export(
    db: AsyncSession, *, user: User, organization: Organization, now: datetime
) -> PersonalDataExport:
    """Everything in the spec's list, read for this person and this organization."""
    entries = await queries.activity(
        db, org_id=organization.id, user_id=user.id, limit=MAX_ACTIVITY_ENTRIES
    )
    truncated = len(entries) == MAX_ACTIVITY_ENTRIES
    return PersonalDataExport(
        exported_at=now,
        profile=ExportedProfile(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            timezone=user.timezone,
            email_verified_at=user.email_verified_at,
            two_factor_enabled=second_factor.is_enabled(user),
            created_at=user.created_at,
        ),
        organization=ExportedOrganization(id=organization.id, name=organization.name),
        invitation_received=(
            ExportedInvitationReceived(invited_by=user.invited_by, invited_at=user.invited_at)
            if user.invited_at is not None
            else None
        ),
        sessions=[
            ExportedSession(
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                client=row.client_label,
                network=row.client_network,
            )
            for row in await queries.live_sessions(
                db, org_id=organization.id, user_id=user.id, now=now
            )
        ],
        activity=[
            ExportedActivity(at=row.at, action=row.action, target=row.target, detail=row.detail)
            for row in entries
        ],
        truncated=truncated,
        oldest_activity_at=entries[-1].at if entries else None,
        api_tokens=[
            ExportedApiToken(
                id=token.id,
                name=token.name,
                role=token.role,
                project_id=token.project_id,
                created_at=token.created_at,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
                last_used_at=token.last_used_at,
            )
            for token in await queries.tokens_created_by(
                db, org_id=organization.id, user_id=user.id
            )
        ],
        invitations_sent=[
            ExportedInvitationSent(
                id=invited.id, email=invited.email, role=invited.role, invited_at=invited.invited_at
            )
            for invited in await queries.invited_by(db, org_id=organization.id, user_id=user.id)
        ],
    )


async def deletion_refusal(db: AsyncSession, *, user: User) -> str | None:
    """Why this person may not delete their account, or None when they may."""
    owners = await members_queries.owners(db, org_id=user.org_id)
    return SOLE_OWNER_REFUSAL if _is_sole_owner(user, owners) else None


async def delete_account(
    db: AsyncSession,
    second_factors: SecondFactorStore,
    box: CredentialBox | None,
    *,
    user: User,
    organization: Organization,
    current_password: str,
    code: str | None,
    now: datetime,
) -> None:
    """Prove it is them, refuse the last owner, then scrub as a removal does.

    The owners are locked, as for a removal, so two owners leaving at once
    cannot both succeed and leave the organization with none.
    """
    if not verify_password(user.password_hash, current_password):
        raise InvalidRequestError(CURRENT_PASSWORD_WRONG)
    if second_factor.is_enabled(user):
        if not code:
            raise InvalidRequestError(CODE_NEEDED)
        if not await second_factor.verify(db, second_factors, box, user=user, code=code):
            raise InvalidRequestError(CODE_WRONG)
    owners = await members_queries.owners_locked(db, org_id=organization.id)
    if _is_sole_owner(user, owners):
        raise ConflictError(SOLE_OWNER_REFUSAL)

    await members.scrub(db, member=user, now=now)
    audit.record_member_left(db, member=user)


def _is_sole_owner(user: User, owners: list[User]) -> bool:
    """An owner with no other owner who can sign in (a pending one cannot)."""
    if user.role != "owner":
        return False
    return not any(owner.id != user.id and owner.password_hash is not None for owner in owners)
