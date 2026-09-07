"""Proving an address, and getting back in without one.

P1-70 and P1-71. Kept out of `service.py` because signing in and recovering an
account are different jobs — `service.py` decides whether a credential is good,
this decides whether somebody controls an address — and the auth package already
splits along those lines with `sessions`, `credentials`, `policy` and `tokens`.

**Two rules run through every function here.**

*Nothing tells a stranger whether an address has an account.* The reset endpoint
answers identically for a known and an unknown address, does the same work for
both, and reveals nothing in timing — `credentials.verify_password` already
carries the same rule for sign-in and the reasoning is quoted there.

*A refusal never explains itself.* `consume` returns `None` for a token that was
never issued, already spent, expired, or minted for the other purpose, and all
four produce one message. Distinguishing them would tell an attacker which
guesses were once real.

**No HTTP and no SQL text**, per the package convention: the router owns the
status code, `queries.py` owns the lookups.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.credentials import hash_password, normalize_email
from nexovue_api.auth.queries import find_active_user_by_email
from nexovue_api.auth.tokens import OneTimeTokenStore, TokenPurpose
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.notifications import EmailError, EmailSender, Message

# One message for every way a link can fail to work. See the module docstring.
LINK_NO_LONGER_VALID: Final = (
    "That link is no longer valid. It may have expired or already been used. Request a new one."
)

# What a recipient reads in the mail. Rendered from the token's own lifetime
# rather than written down twice, so changing the TTL cannot leave the message
# claiming something else.
_SECONDS_PER_HOUR: Final = 3600


def describe_lifetime(ttl_seconds: int) -> str:
    """A link's lifetime in the words a customer reads.

    Hours, because both lifetimes are hours and a link measured in seconds
    reads as a system talking to itself. Rounded down deliberately: telling
    someone they have an hour when they have fifty-nine minutes is the safe
    direction to be wrong in.
    """
    hours = max(1, ttl_seconds // _SECONDS_PER_HOUR)
    return "1 hour" if hours == 1 else f"{hours} hours"


async def send_verification(
    tokens: OneTimeTokenStore,
    email: EmailSender,
    *,
    user: User,
    app_base_url: str,
    ttl_seconds: int,
) -> None:
    """Mail a fresh verification link to a user's own address.

    Sending to `user.email` and never to an address the caller supplied: a
    caller-chosen recipient would let anybody have our verified-domain mail sent
    wherever they liked.

    An already-verified address is sent nothing rather than refused. The caller
    is a person clicking "resend", and telling them off for an address that is
    already fine helps nobody.
    """
    if user.email_verified_at is not None:
        return

    token = await tokens.issue(
        purpose=TokenPurpose.VERIFY_EMAIL, user_id=str(user.id), ttl_seconds=ttl_seconds
    )
    await email.send(
        to=user.email,
        message=Message.VERIFY_EMAIL,
        data={
            "link": _link(app_base_url, "verify-email", token),
            "expires_in": describe_lifetime(ttl_seconds),
        },
    )


async def verify_email(db: AsyncSession, tokens: OneTimeTokenStore, *, token: str) -> None:
    """Spend a verification token and stamp the address as proven.

    Writes the `email_verified_at` that has existed unwritten since migration
    0001. Verification deliberately gates nothing in Phase 1 — the plan's own
    reasoning is that verifying *after* signup *"keeps the first-span
    celebration intact, which is the one moment onboarding earns"* — so this
    records a fact rather than unlocking a door.
    """
    user = await _user_behind(db, tokens, purpose=TokenPurpose.VERIFY_EMAIL, token=token)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        audit.record_email_verified(db, user=user)


async def request_password_reset(
    db: AsyncSession,
    tokens: OneTimeTokenStore,
    email: EmailSender,
    *,
    address: str,
    app_base_url: str,
    ttl_seconds: int,
) -> None:
    """Mail a reset link if the address has an account, and say nothing either way.

    The caller must answer the same regardless, so this returns `None` in both
    cases and raises only when the *provider* failed — which is our fault and
    not a fact about the address.
    """
    found = await find_active_user_by_email(db, normalize_email(address))
    if found is None:
        return

    user, _ = found
    token = await tokens.issue(
        purpose=TokenPurpose.RESET_PASSWORD, user_id=str(user.id), ttl_seconds=ttl_seconds
    )
    await email.send(
        to=user.email,
        message=Message.RESET_PASSWORD,
        data={
            "link": _link(app_base_url, "reset-password", token),
            "expires_in": describe_lifetime(ttl_seconds),
        },
    )
    audit.record_password_reset_requested(db, user=user)


async def reset_password(
    db: AsyncSession, tokens: OneTimeTokenStore, *, token: str, new_password: str
) -> User:
    """Spend a reset token, set the password, and end every live session.

    **The session revocation is the half that is easy to leave out.** Somebody
    resetting a password may be doing it because an attacker holds their old
    one, and an attacker with a live session does not need the password again.
    `sessions_valid_from` is what ends them: Redis keys sessions by token
    fingerprint, so there is nothing to enumerate, and one write invalidates
    every session in existence for this user (migration 0019).

    The session the *caller* is holding, if any, is ended with the rest. The
    router signs them in again or does not; what must not happen is a reset that
    leaves older sessions alive because one of them was convenient.
    """
    user = await _user_behind(db, tokens, purpose=TokenPurpose.RESET_PASSWORD, token=token)
    user.password_hash = hash_password(new_password)
    user.sessions_valid_from = datetime.now(UTC)
    # A reset proves control of the address as surely as following a
    # verification link does, so an unverified account becomes verified here
    # rather than leaving the customer a second hoop after the same proof.
    if user.email_verified_at is None:
        user.email_verified_at = user.sessions_valid_from
    audit.record_password_reset_completed(db, user=user)
    return user


async def _user_behind(
    db: AsyncSession, tokens: OneTimeTokenStore, *, purpose: TokenPurpose, token: str
) -> User:
    """The live user a token names, or one refusal covering every failure."""
    user_id = await tokens.consume(purpose=purpose, token=token)
    if user_id is None:
        raise InvalidRequestError(LINK_NO_LONGER_VALID)

    user = await db.get(User, _as_uuid(user_id))
    if user is None:
        raise InvalidRequestError(LINK_NO_LONGER_VALID)

    # Deletion is at the organization, not the user — `find_active_user_by_email`
    # filters on `Organization.deleted_at` for the same reason. A link that
    # outlived its tenant is refused with the one message: the row can be gone
    # because the organization was deleted while the mail sat in an inbox, and
    # the customer's remedy is the same either way.
    organization = await db.get(Organization, user.org_id)
    if organization is None or organization.deleted_at is not None:
        raise InvalidRequestError(LINK_NO_LONGER_VALID)
    return user


def _as_uuid(value: str) -> uuid.UUID:
    """The id as stored. Separate so a malformed value cannot reach `db.get` as a
    string and surface as a database error instead of the one refusal."""
    try:
        return uuid.UUID(value)
    except ValueError as malformed:
        raise InvalidRequestError(LINK_NO_LONGER_VALID) from malformed


def _link(app_base_url: str, path: str, token: str) -> str:
    """The URL a customer clicks.

    It points at the **app**, not this API: the customer lands on a screen that
    then calls the endpoint. The token is a query parameter because it must
    survive being pasted out of a mail client, and the screen reads it there.
    """
    return f"{app_base_url.rstrip('/')}/{path}?token={token}"


__all__ = [
    "LINK_NO_LONGER_VALID",
    "EmailError",
    "describe_lifetime",
    "request_password_reset",
    "reset_password",
    "send_verification",
    "verify_email",
]
