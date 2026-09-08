"""Proving an address, and getting back in without one.

P1-70, P1-71 and the change-of-address half of P1-72. Kept out of `service.py`
because signing in and recovering an account are different jobs — `service.py`
decides whether a credential is good, this decides whether somebody controls an
address — and the auth package already splits along those lines with `sessions`,
`credentials`, `policy` and `tokens`.

**Changing an address is the same proof pointed at a different inbox**, which is
why it is here and not a package of its own. Two rules shape it and each is a
failure it prevents:

*The link goes to the new address.* Mailing the old one proves nothing about the
new one, and proving control of the new one is the entire point.

*Nothing on the account changes until the link is followed.* Until then the
customer keeps the address they can receive mail at. A change that took effect
immediately and then failed verification would leave somebody locked out of an
inbox they cannot read — the outcome P1-70 exists to prevent, arrived at from
the other direction.

**Two rules run through every function here.**

*Nothing tells a stranger whether an address has an account.* The reset endpoint
answers identically for a known and an unknown address, **including when the
mail provider fails**, which is the part this file originally got wrong: a send
that raised escaped as a 5xx for a known address while an unknown one still got
202, so a provider having a bad minute turned the form into the oracle the
design exists to prevent. `credentials.verify_password` carries the same rule for
sign-in and the reasoning is quoted there.

**What is *not* claimed is constant time.** The known path makes a network call
and the unknown path returns immediately, so the two are distinguishable by
somebody measuring carefully. Equalising that would mean doing a fake send, and a
fake send is a thing that can itself fail and be measured. What bounds the attack
instead is the rate limiter: `/auth/password-reset` sits on `AUTH_TIER`, ten a
minute per address and per IP, which makes enumeration by stopwatch expensive
enough to be a poor way to learn something a signup form would also reveal. This
paragraph replaces an earlier sentence claiming the timing revealed nothing,
which was untrue when written.

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

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.credentials import hash_password, is_valid_email, normalize_email
from nexovue_api.auth.queries import find_active_user_by_email
from nexovue_api.auth.tokens import OneTimeTokenStore, TokenPurpose
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError
from nexovue_api.notifications import EmailError, EmailSender, Message

# `users.email` is globally unique, and both of these already say what a person
# should do about a collision. Imported rather than restated: a second wording
# for one constraint is a second thing to keep true. They belong in `auth`
# rather than in `signup` — see the note in the P1-72 handover.
from nexovue_api.signup.queries import email_is_taken
from nexovue_api.signup.service import EMAIL_INVALID_DETAIL, EMAIL_TAKEN_DETAIL

_log = structlog.get_logger(__name__)

# One message for every way a link can fail to work. See the module docstring.
LINK_NO_LONGER_VALID: Final = (
    "That link is no longer valid. It may have expired or already been used. Request a new one."
)

# Refused rather than accepted as a no-op, for the same reason a password change
# refuses the password already set: it is almost always a mis-paste, and mailing
# a "confirm this address" link for the address somebody already has reads as
# the product being confused.
EMAIL_ALREADY_YOURS: Final = "That is already the address on this account."

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


async def send_email_change_link(
    db: AsyncSession,
    tokens: OneTimeTokenStore,
    email: EmailSender,
    *,
    user: User,
    new_address: str,
    app_base_url: str,
    ttl_seconds: int,
) -> None:
    """Mail a confirmation link to the address a customer wants to move to.

    **Nothing on the account is written here.** The address changes when the
    link is followed and not before, so a customer who mistypes the address
    keeps the one they can still read mail at, and a request they abandon
    expires on its own with nothing to clean up.

    **The collision is answered here rather than by the unique index.** Refusing
    an address that already has an account is a small piece of "this address is
    taken", told to somebody who has already proved they hold this account's
    password — not the anonymous oracle `request_password_reset` is shaped to
    avoid. The alternative is a customer who typed their spouse's address by
    mistake waiting forever for a mail that our own database guaranteed would
    never be sent. `confirm_email_change` checks again, because between the two
    calls somebody else can take it.

    The caller is expected to have proved the current password already; see
    `profile.service.request_email_change` for why that gate exists.
    """
    address = normalize_email(new_address)
    if not is_valid_email(address):
        raise InvalidRequestError(EMAIL_INVALID_DETAIL)
    if address == user.email:
        raise InvalidRequestError(EMAIL_ALREADY_YOURS)
    if await email_is_taken(db, address):
        raise ConflictError(EMAIL_TAKEN_DETAIL)

    token = await tokens.issue_email_change(
        user_id=str(user.id), new_email=address, ttl_seconds=ttl_seconds
    )
    await email.send(
        to=address,
        message=Message.VERIFY_EMAIL,
        data={
            "link": _link(app_base_url, "email-change", token),
            "expires_in": describe_lifetime(ttl_seconds),
        },
    )
    audit.record_email_change_requested(db, user=user)


async def confirm_email_change(db: AsyncSession, tokens: OneTimeTokenStore, *, token: str) -> User:
    """Spend a change-of-address link and move the account onto it.

    **This is the write the whole flow was deferring.** The address arrives from
    the token rather than from the row, so a link applies the address it was
    minted for even if a later request named a different one.

    The new address is stamped verified in the same breath: following this link
    is the same proof `verify_email` accepts, and asking for it twice would be
    asking somebody to prove the same thing again.

    **No session is ended.** A reset ends every session because the password may
    be in somebody else's hands; nothing here says that. The gate that protects
    this flow is the current password, checked when the link was asked for.
    """
    change = await tokens.consume_email_change(token=token)
    if change is None:
        raise InvalidRequestError(LINK_NO_LONGER_VALID)
    user = await _live_user(db, change.user_id)

    if user.email != change.new_email:
        if await email_is_taken(db, change.new_email):
            raise ConflictError(EMAIL_TAKEN_DETAIL)
        user.email = change.new_email
        audit.record_email_changed(db, user=user)
    user.email_verified_at = datetime.now(UTC)
    await _flush_or_refuse(db)
    return user


async def _flush_or_refuse(db: AsyncSession) -> None:
    """Write now, so a lost race is a message and not a 500.

    The unique index on `users.email` is the guarantee; the check above only
    makes the common case a sentence somebody can act on. Without this flush the
    violation would surface from the request dependency's commit, long after any
    handler could turn it into one.
    """
    try:
        await db.flush()
    except IntegrityError as collided:
        raise ConflictError(EMAIL_TAKEN_DETAIL) from collided


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

    **Returns `None` on every path, including a provider that refused.** An
    earlier version let `EmailError` escape, which meant a known address got a
    5xx and an unknown one got 202 whenever the provider was unhealthy — the
    account-existence oracle this endpoint is shaped to avoid, appearing exactly
    when somebody is having a bad day and would not think to look.

    A failure is loud in the log and silent in the response, which is the only
    split that keeps both properties: the operator learns the provider is
    refusing, and the caller learns nothing about the address.

    An unconfigured provider is deliberately *not* handled here either — the
    `EmailDelivery` dependency answers 503 before this function runs, so it fails
    the request identically for every address and carries no signal.
    """
    found = await find_active_user_by_email(db, normalize_email(address))
    if found is None:
        return

    user, _ = found
    token = await tokens.issue(
        purpose=TokenPurpose.RESET_PASSWORD, user_id=str(user.id), ttl_seconds=ttl_seconds
    )
    try:
        await email.send(
            to=user.email,
            message=Message.RESET_PASSWORD,
            data={
                "link": _link(app_base_url, "reset-password", token),
                "expires_in": describe_lifetime(ttl_seconds),
            },
        )
    except EmailError as refused:
        # Loud here, silent to the caller. See the docstring: raising would tell
        # a stranger the address exists.
        _log.error(
            "a password reset link could not be sent; the customer has been told nothing",
            user_id=str(user.id),
            error=str(refused),
        )
        return
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
    return await _live_user(db, user_id)


async def _live_user(db: AsyncSession, user_id: str) -> User:
    """The user an already-spent token named, if their tenant is still here."""
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
    "EMAIL_ALREADY_YOURS",
    "LINK_NO_LONGER_VALID",
    "EmailError",
    "confirm_email_change",
    "describe_lifetime",
    "request_password_reset",
    "reset_password",
    "send_email_change_link",
    "send_verification",
    "verify_email",
]
