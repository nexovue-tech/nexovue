"""Bringing people into an organization, changing what they may do, and removing them.

**Three rules hold on every path** (spec BR2, BR3, BR5):

- *Nobody grants more than they hold.* A role can be given only by someone whose
  own role is at least that high, and an owner is made or unmade only by an owner.
- *Always an owner.* No demotion or removal may leave the organization without an
  owner who can sign in; the owners are locked while that is decided.
- *Removal keeps history.* The row stays — the audit log and "muted by" name it
  — while its address is rewritten so the person can sign up again, and the
  sessions epoch ends every session they hold on its next request.

No HTTP here; the router commits before an email goes, so a link is never sent
for a person who does not exist yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Final

import structlog
from nexovue_platform.email import EmailError, EmailSender, Message
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import set_password
from nexovue_api.auth.credentials import is_valid_email, normalize_email
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from nexovue_api.members import queries
from nexovue_api.members.schemas import (
    MemberListResponse,
    MemberStatus,
    MemberSummary,
    Role,
)

logger = structlog.get_logger(__name__)

# D5. Long enough for a weekend, short enough that a forwarded mail goes stale.
INVITE_LIFETIME: Final = timedelta(days=7)

_RANK: Final = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}

NO_SUCH_MEMBER: Final = "No such person in this organization."
EMAIL_INVALID: Final = "That is not an email address."
ALREADY_A_MEMBER: Final = "Already a member of this organization."
ALREADY_INVITED: Final = "Already invited. Resend the invite instead."
ELSEWHERE: Final = (
    "This address already belongs to another nexovue organization. Invite a different address."
)
NOT_PENDING: Final = "This person has already joined; there is no invite to resend or withdraw."
PENDING: Final = "This person has not joined yet. Withdraw the invite instead."
ONLY_AN_OWNER_MAKES_OWNERS: Final = "Only an owner can make someone an owner, or change one."


async def list_members(db: AsyncSession, *, org_id: uuid.UUID, now: datetime) -> MemberListResponse:
    members = await queries.members_of(db, org_id=org_id)
    names = {member.id: display_name(member) for member in members}
    signed_in = await queries.last_sign_ins(
        db, org_id=org_id, user_ids=[member.id for member in members]
    )
    return MemberListResponse(
        members=[
            describe(
                member,
                now=now,
                invited_by=names.get(member.invited_by) if member.invited_by else None,
                last_signed_in_at=signed_in.get(member.id),
            )
            for member in members
        ]
    )


async def invite(
    db: AsyncSession, *, actor: User, email: str, role: Role, now: datetime
) -> tuple[User, str]:
    """A new person in `actor`'s organization, and the plaintext of their link."""
    _may_grant(actor, role)
    address = normalize_email(email)
    if not is_valid_email(address):
        raise InvalidRequestError(EMAIL_INVALID)
    existing = await queries.find_by_email(db, address)
    if existing is not None:
        if existing.org_id != actor.org_id:
            raise ConflictError(ELSEWHERE)
        raise ConflictError(ALREADY_INVITED if _is_pending(existing) else ALREADY_A_MEMBER)

    member = User(
        org_id=actor.org_id,
        email=address,
        name=None,
        password_hash=None,
        role=role,
        invited_by=actor.id,
        invited_at=now,
    )
    token = set_password.issue(member, now=now, lifetime=INVITE_LIFETIME)
    db.add(member)
    try:
        await db.flush()
    except IntegrityError as taken:
        # Two invites to one address racing: the unique address decides.
        raise ConflictError(ALREADY_INVITED) from taken
    audit.record_member_invited(db, member=member, actor_user_id=actor.id)
    return member, token


async def resend(
    db: AsyncSession, *, actor: User, member_id: uuid.UUID, now: datetime
) -> tuple[User, str]:
    """A new link for a pending invite; the old one stops working."""
    member = await _pending(db, actor=actor, member_id=member_id)
    _may_grant(actor, member.role)
    token = set_password.issue(member, now=now, lifetime=INVITE_LIFETIME)
    member.invite_sent_at = None
    audit.record_member_invite_resent(db, member=member, actor_user_id=actor.id)
    return member, token


async def withdraw(db: AsyncSession, *, actor: User, member_id: uuid.UUID) -> None:
    """Delete a never-accepted invite. Nothing references a person who never joined."""
    member = await _pending(db, actor=actor, member_id=member_id)
    _may_grant(actor, member.role)
    audit.record_member_invite_withdrawn(db, member=member, actor_user_id=actor.id)
    await db.delete(member)


async def mark_sent(db: AsyncSession, *, member: User, now: datetime) -> None:
    member.invite_sent_at = now
    await db.flush()


async def change_role(
    db: AsyncSession,
    *,
    actor: User,
    organization: Organization,
    member_id: uuid.UUID,
    role: Role,
    now: datetime,
) -> MemberSummary:
    member = await _member(db, org_id=actor.org_id, member_id=member_id, lock=True)
    if member.role != role:
        _may_act_on(actor, member)
        _may_grant(actor, role)
        if member.role == "owner":
            await _refuse_leaving_no_owner(db, organization=organization, leaving=member)
        before = member.role
        member.role = role
        audit.record_member_role_changed(db, member=member, actor_user_id=actor.id, before=before)
        await db.flush()
    return describe(member, now=now, invited_by=None, last_signed_in_at=None)


async def remove(
    db: AsyncSession,
    *,
    actor: User,
    organization: Organization,
    member_id: uuid.UUID,
    now: datetime,
) -> None:
    member = await _member(db, org_id=actor.org_id, member_id=member_id, lock=True)
    if _is_pending(member):
        raise ConflictError(PENDING)
    _may_act_on(actor, member)
    if member.role == "owner":
        await _refuse_leaving_no_owner(db, organization=organization, leaving=member)

    address = member.email
    member.removed_at = now
    # The sessions epoch: every session this person holds is refused on its
    # next request, in one write.
    member.sessions_valid_from = now
    # Frees the address (it is globally unique) so the person can sign up again
    # or be invited back as someone new; `name` stays for history (FR11, BR6).
    member.email = f"removed+{member.id}@removed.invalid"
    set_password.clear(member)
    audit.record_member_removed(db, member=member, actor_user_id=actor.id, email=address)


async def mail_invitation(
    email: EmailSender | None,
    *,
    member: User,
    token: str,
    organization: Organization,
    inviter: User,
    app_base_url: str,
) -> bool:
    """Send the invite. Never fails the request: an unsent invite is shown as
    *not sent* with a resend, which is recoverable; a lost invite row is not."""
    if email is None:
        logger.warning("invite created without mail: no email provider configured")
        return False
    try:
        await email.send(
            to=member.email,
            message=Message.INVITE,
            data={
                "inviter": display_name(inviter),
                "organization": organization.name,
                "role_phrase": _role_phrase(member.role),
                "link": f"{app_base_url.rstrip('/')}/invite?token={token}",
                "expires_in": "7 days",
            },
            idempotency_key=f"invite:{set_password.hash_link(token)}",
        )
    except EmailError as failure:
        logger.error("invite mail refused; the inviter can resend", error=str(failure))
        return False
    return True


def describe(
    member: User,
    *,
    now: datetime,
    invited_by: str | None,
    last_signed_in_at: datetime | None,
) -> MemberSummary:
    return MemberSummary(
        id=member.id,
        email=member.email,
        name=member.name,
        role=member.role,
        status=_status(member, now=now),
        invited_by=invited_by,
        invited_at=member.invited_at,
        invite_sent_at=member.invite_sent_at if _is_pending(member) else None,
        invite_expires_at=member.set_password_expires_at if _is_pending(member) else None,
        added_at=member.created_at,
        last_signed_in_at=last_signed_in_at,
    )


def _status(member: User, *, now: datetime) -> MemberStatus:
    if not _is_pending(member):
        return "active"
    expires_at = member.set_password_expires_at
    return "expired" if expires_at is None or expires_at <= now else "invited"


def _is_pending(member: User) -> bool:
    return member.password_hash is None


async def _member(
    db: AsyncSession, *, org_id: uuid.UUID, member_id: uuid.UUID, lock: bool = False
) -> User:
    member = await queries.find_member(db, org_id=org_id, user_id=member_id, lock=lock)
    if member is None:
        raise NotFoundError(NO_SUCH_MEMBER)
    return member


async def _pending(db: AsyncSession, *, actor: User, member_id: uuid.UUID) -> User:
    member = await _member(db, org_id=actor.org_id, member_id=member_id, lock=True)
    if not _is_pending(member):
        raise ConflictError(NOT_PENDING)
    return member


def _may_grant(actor: User, role: str) -> None:
    """BR3: at most the actor's own role, and an owner only from an owner."""
    if role == "owner" and not may(actor.role, Action.MANAGE_OWNERS):
        raise PermissionDeniedError(ONLY_AN_OWNER_MAKES_OWNERS)
    if _RANK[role] > _RANK[actor.role]:
        raise PermissionDeniedError(f"You can give at most your own role ({actor.role}).")


def _may_act_on(actor: User, member: User) -> None:
    """Nobody changes or removes someone whose role is above their own."""
    if member.role == "owner" and not may(actor.role, Action.MANAGE_OWNERS):
        raise PermissionDeniedError(ONLY_AN_OWNER_MAKES_OWNERS)
    if _RANK[member.role] > _RANK[actor.role]:
        raise PermissionDeniedError(
            f"Only someone whose role is at least {member.role} can change this person."
        )


async def _refuse_leaving_no_owner(
    db: AsyncSession, *, organization: Organization, leaving: User
) -> None:
    """BR2. An owner who can sign in must remain; a pending one cannot."""
    owners = await queries.owners_locked(db, org_id=organization.id)
    remaining = [owner for owner in owners if owner.id != leaving.id and not _is_pending(owner)]
    if not remaining:
        raise ConflictError(
            f"{organization.name} needs at least one owner. Make someone else an owner first."
        )


def display_name(user: User) -> str:
    return user.name or user.email


def record_joined(db: AsyncSession, *, member: User) -> None:
    audit.record_member_joined(db, member=member)


def _role_phrase(role: str) -> str:
    return f"an {role}" if role[0] in "aeiou" else f"a {role}"
