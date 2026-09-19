"""The members API (F26). HTTP only: the rules are in `service.py`.

Two routers. `router` is for a signed-in organization; `invitation_router` is
reached from an email by someone who is not signed in yet, and the link's token
in the body is its whole credential.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status
from nexovue_platform.email import EmailSender

from nexovue_api.auth import service as auth_service
from nexovue_api.auth import set_password
from nexovue_api.auth.cookies import set_session_cookie
from nexovue_api.auth.dependencies import CurrentClient, CurrentIdentity, Identity
from nexovue_api.auth.policy import MayManageMembers
from nexovue_api.auth.schemas import IdentityResponse, TokenSubmission
from nexovue_api.data.postgres.models import User
from nexovue_api.deps import AppSettings, DbSession, OptionalEmail, SessionStoreDep
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.members import service
from nexovue_api.members.schemas import (
    AcceptInvitation,
    InvitationPreview,
    InviteRequest,
    MemberListResponse,
    MemberSummary,
    RoleChangeRequest,
)

router = APIRouter(prefix="/members", tags=["members"])
invitation_router = APIRouter(prefix="/invitations", tags=["members"])


@router.get("", summary="Everyone in this organization, and every pending invite")
async def list_members(identity: CurrentIdentity, db: DbSession) -> MemberListResponse:
    return await service.list_members(db, org_id=identity.org_id, now=datetime.now(UTC))


@router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone by email, with a role",
)
async def invite_member(
    identity: MayManageMembers,
    db: DbSession,
    request: InviteRequest,
    email: OptionalEmail,
    settings: AppSettings,
) -> MemberSummary:
    now = datetime.now(UTC)
    member, token = await service.invite(
        db, actor=identity.user, email=request.email, role=request.role, now=now
    )
    # Committed before the mail goes, so a link never names a person who was
    # rolled back.
    await db.commit()
    await _send(db, identity, member, token, email, settings.app_base_url, now)
    return service.describe(
        member, now=now, invited_by=service.display_name(identity.user), last_signed_in_at=None
    )


@router.post(
    "/invitations/{member_id}/resend",
    summary="Send a pending invite again, with a new link",
)
async def resend_invitation(
    identity: MayManageMembers,
    db: DbSession,
    member_id: uuid.UUID,
    email: OptionalEmail,
    settings: AppSettings,
) -> MemberSummary:
    now = datetime.now(UTC)
    member, token = await service.resend(db, actor=identity.user, member_id=member_id, now=now)
    await db.commit()
    await _send(db, identity, member, token, email, settings.app_base_url, now)
    return service.describe(member, now=now, invited_by=None, last_signed_in_at=None)


@router.delete(
    "/invitations/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a pending invite",
)
async def withdraw_invitation(
    identity: MayManageMembers, db: DbSession, member_id: uuid.UUID
) -> None:
    await service.withdraw(db, actor=identity.user, member_id=member_id)


@router.patch("/{member_id}", summary="Change someone's role")
async def change_role(
    identity: MayManageMembers,
    db: DbSession,
    member_id: uuid.UUID,
    request: RoleChangeRequest,
) -> MemberSummary:
    return await service.change_role(
        db,
        actor=identity.user,
        organization=identity.organization,
        member_id=member_id,
        role=request.role,
        now=datetime.now(UTC),
    )


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove someone; their sessions end and their work stays",
)
async def remove_member(identity: MayManageMembers, db: DbSession, member_id: uuid.UUID) -> None:
    await service.remove(
        db,
        actor=identity.user,
        organization=identity.organization,
        member_id=member_id,
        now=datetime.now(UTC),
    )


@invitation_router.post("/preview", summary="What an invite link is for, before accepting")
async def preview_invitation(payload: TokenSubmission, db: DbSession) -> InvitationPreview:
    holder = await set_password.holder_of(db, payload.token, now=datetime.now(UTC))
    expires_at = holder.user.set_password_expires_at
    if expires_at is None:
        # A live link always has one (CHECK constraint); refuse rather than guess.
        raise InvalidRequestError(set_password.LINK_NO_LONGER_VALID)
    return InvitationPreview(
        organization=holder.organization.name,
        email=holder.user.email,
        role=holder.user.role,
        invited_by=set_password.display_name(holder.invited_by),
        expires_at=expires_at,
    )


@invitation_router.post("/accept", summary="Choose a password and join")
async def accept_invitation(
    payload: AcceptInvitation,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    client: CurrentClient,
    settings: AppSettings,
) -> IdentityResponse:
    holder = await set_password.set_password(
        db, payload.token, name=payload.name, password=payload.password, now=datetime.now(UTC)
    )
    service.record_joined(db, member=holder.user)
    token = await auth_service.start_session(db, sessions, user=holder.user, client=client)
    set_session_cookie(response, token, settings)
    return auth_service.describe_identity(holder.user, holder.organization)


async def _send(
    db: DbSession,
    identity: Identity,
    member: User,
    token: str,
    email: EmailSender | None,
    app_base_url: str,
    now: datetime,
) -> None:
    sent = await service.mail_invitation(
        email,
        member=member,
        token=token,
        organization=identity.organization,
        inviter=identity.user,
        app_base_url=app_base_url,
    )
    if sent:
        await service.mark_sent(db, member=member, now=now)
