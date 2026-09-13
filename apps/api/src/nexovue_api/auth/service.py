"""Signing in, signing out, and answering who is signed in.

No HTTP and no SQL text: the router owns the cookie, `queries.py` owns the
lookups, and what is left here is the decision.

The session store is driven from here rather than from the routers. Login and
signup both mint a session, and a session that is created is a security event —
putting the mint in one place is what keeps its audit record from depending on
which door the caller came through. The database session is named `db`
throughout, because a browser session is also a session in this module.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import second_factor
from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.credentials import normalize_email, verify_password
from nexovue_api.auth.encryption import CredentialBox
from nexovue_api.auth.queries import find_active_user_by_email, load_identity
from nexovue_api.auth.schemas import IdentityResponse, OrganizationSummary, UserSummary
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import UnauthenticatedError

# One string for both failures. An unknown email and a wrong password must be
# indistinguishable, or the endpoint tells an attacker which addresses exist.
INVALID_CREDENTIALS_DETAIL: Final = "That email and password do not match an account."

# One string for every way the second half of a sign-in can fail. See
# `authenticate_second_factor`.
SECOND_FACTOR_NOT_ACCEPTED: Final = (
    "That code was not accepted. Sign in again to start a new attempt."
)


async def authenticate(db: AsyncSession, *, email: str, password: str) -> tuple[User, Organization]:
    """The user behind these credentials, or a 401 that reveals nothing."""
    found = await find_active_user_by_email(db, normalize_email(email))
    stored_hash = found[0].password_hash if found is not None else None
    # Always verify, even with no user: see `credentials.verify_password`.
    if not verify_password(stored_hash, password) or found is None:
        await _record_refusal(db, user=found[0] if found is not None else None)
        raise UnauthenticatedError(INVALID_CREDENTIALS_DETAIL)
    return found


async def authenticate_second_factor(
    db: AsyncSession,
    second_factors: SecondFactorStore,
    box: CredentialBox | None,
    *,
    challenge_token: str,
    code: str,
) -> tuple[User, Organization]:
    """The user behind a challenge and a code, or a 401 that reveals nothing.

    **One refusal for every failure.** A challenge that was never issued, one
    that expired, one whose five attempts are gone, a wrong code, a spent
    recovery code and a replayed one all produce `SECOND_FACTOR_NOT_ACCEPTED`.
    Telling them apart would tell an attacker who already holds the password
    which half of the attack is landing.

    **A wrong code costs the challenge an attempt**, and the fifth ends it. The
    rate limiter bounds how fast attempts can be made; this bounds how many one
    password submission is worth, which is the part a limiter cannot express.

    The user is re-read from Postgres rather than carried through the challenge:
    between the two halves of a sign-in the account may have been deleted, its
    organization closed, or its second factor turned off, and the state that
    matters is the one at the moment the session is minted.
    """
    challenge = await second_factors.read_challenge(token=challenge_token)
    if challenge is None:
        raise UnauthenticatedError(SECOND_FACTOR_NOT_ACCEPTED)

    found = await _live_identity(db, challenge.user_id)
    if found is None:
        await second_factors.close_challenge(token=challenge_token)
        raise UnauthenticatedError(SECOND_FACTOR_NOT_ACCEPTED)

    user, organization = found
    if not await second_factor.verify(db, second_factors, box, user=user, code=code):
        await second_factors.record_failed_attempt(token=challenge_token, challenge=challenge)
        await _record_refusal(db, user=user)
        raise UnauthenticatedError(SECOND_FACTOR_NOT_ACCEPTED)

    await second_factors.close_challenge(token=challenge_token)
    return user, organization


async def _live_identity(db: AsyncSession, user_id: str) -> tuple[User, Organization] | None:
    """The user a challenge names, if they and their tenant are still here."""
    try:
        identifier = uuid.UUID(user_id)
    except ValueError:
        return None
    user = await db.get(User, identifier)
    if user is None:
        return None
    return await load_identity(db, user_id=user.id, org_id=user.org_id)


async def _record_refusal(db: AsyncSession, *, user: User | None) -> None:
    """Write the refusal and commit it, before the 401 unwinds the request.

    The one place in this codebase that commits outside the request dependency,
    and deliberately. Every other entry joins the transaction of the action it
    records so the two are atomic; a refused sign-in has no action to be atomic
    with, and the exception that follows would roll the entry back — leaving
    brute force unrecorded, which is the whole reason to write it.
    """
    audit.record_session_denied(db, user=user)
    await db.commit()


async def start_session(
    db: AsyncSession,
    sessions: SessionStore,
    *,
    user: User,
    client: ClientDescription | None = None,
) -> str:
    """Mint a session for a user and record it. The token goes in the cookie.

    `client` is what the session will be recognised by in the customer's own
    list, already reduced to a browser family and a network by
    `auth/clients.py`. It is optional because a caller that has no request in
    hand should record nothing rather than invent something: an unlabelled
    session reads as "Unknown device", which is honest.
    """
    token, _ = await sessions.create(user_id=str(user.id), org_id=str(user.org_id), client=client)
    audit.record_session_created(db, user=user)
    return token


async def end_session(db: AsyncSession, sessions: SessionStore, token: str | None) -> None:
    """Give up the session a token names. A token that names none does nothing.

    Who it belonged to is read from the session store rather than from the
    request, so signing out records the user whose session actually ended.
    """
    if token is None:
        return

    ending = await sessions.get(token)
    await sessions.revoke(token)
    if ending is None:
        return

    try:
        org_id, user_id = uuid.UUID(ending.org_id), uuid.UUID(ending.user_id)
    except ValueError:
        # A session whose ids are not ids names nobody, so there is nobody to record.
        return
    audit.record_session_ended(db, org_id=org_id, user_id=user_id)


def describe_user(user: User) -> UserSummary:
    """One place a `UserSummary` is built.

    It exists because there were two, and adding `email_verified_at` to the
    schema broke the other one at runtime rather than at type-check time —
    Pydantic validates on construction, so a missing field is a 500 and not a
    compiler error. Every caller comes here so the next field cannot do that.
    """
    return UserSummary(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        timezone=user.timezone,
        email_verified_at=user.email_verified_at,
    )


def describe_identity(user: User, organization: Organization) -> IdentityResponse:
    """The shared body of login, signup's session, and `/auth/me`."""
    return IdentityResponse(
        user=describe_user(user),
        organization=OrganizationSummary(
            id=organization.id, slug=organization.slug, name=organization.name
        ),
    )
