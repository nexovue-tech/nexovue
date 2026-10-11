"""Proving, again, that it is you — the gate on the platform surface.

A session is a seven-day credential. The platform surface — onboarding a
customer, suspending ingest, comping a plan, viewing a console as someone — is
not something a seven-day credential should reach on its own, and since the
admin screens live on the customer origin an XSS in the console would otherwise
inherit it (`docs/changes/super-admin-in-the-console/`).

So the surface asks for the password, or a second-factor code, and stamps the
session for thirty minutes. A ridden session reaches the customer console and
stops there; it is GitLab's "Admin Mode" by another name.

The stamp lives on the session record, so signing out revokes it and no second
store can disagree about whether it is live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import second_factor
from nexovue_api.auth.credentials import verify_password
from nexovue_api.auth.encryption import CredentialBox
from nexovue_api.auth.second_factor import verify as verify_second_factor
from nexovue_api.auth.sessions import Session, SessionStore
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.data.postgres.models import SUPER_ADMIN_ROLE, User
from nexovue_api.errors.exceptions import PermissionDeniedError, UnauthenticatedError

# Long enough to onboard a customer without re-typing a password; short enough
# that a session left open in a coffee shop is not a platform session.
ADMIN_MODE_MINUTES: Final = 30

NOT_THE_OPERATOR: Final = "Only nexovue's operator can administer the platform."
WRONG_CREDENTIAL: Final = "That password or code is not right."
# The operator enrolled a second factor and offered only the password.
CODE_REQUIRED: Final = (
    "Two-factor sign-in is on for this account: enter a code from your authenticator."
)
SESSION_GONE: Final = "This session has expired. Sign in again."
NEEDS_RE_AUTHENTICATION: Final = (
    "Confirm it is you before administering the platform: this session has not "
    "been re-verified in the last 30 minutes."
)


def is_the_operator(user: User) -> bool:
    return user.role == SUPER_ADMIN_ROLE


async def enter(
    db: AsyncSession,
    sessions: SessionStore,
    factors: SecondFactorStore,
    box: CredentialBox | None,
    *,
    user: User,
    token: str | None,
    password: str | None,
    code: str | None,
    now: datetime | None = None,
) -> datetime:
    """Re-authenticate and stamp the session. Returns when the stamp lapses.

    A second-factor code when the operator has enrolled one — the password alone
    is then refused (group 9, A3) — and otherwise the password. Neither
    is accepted from anyone but the operator: a member with the right password
    is refused here, so this endpoint cannot be used to discover the role.
    """
    moment = now or datetime.now(UTC)
    if not is_the_operator(user):
        raise PermissionDeniedError(NOT_THE_OPERATOR)
    if token is None:
        raise UnauthenticatedError(SESSION_GONE)

    if not await _proved(db, factors, box, user=user, password=password, code=code):
        audit.record_admin_mode_refused(db, user=user)
        # Committed before the refusal unwinds the request, as a denied sign-in
        # is (`auth/service.py`): the exception would otherwise roll the entry
        # back, and somebody guessing at the platform gate is exactly what this
        # row exists to show.
        await db.commit()
        if not code and second_factor.is_enabled(user):
            raise PermissionDeniedError(CODE_REQUIRED)
        raise PermissionDeniedError(WRONG_CREDENTIAL)

    until = moment + timedelta(minutes=ADMIN_MODE_MINUTES)
    if await sessions.set_admin_until(token, until) is None:
        raise UnauthenticatedError(SESSION_GONE)
    audit.record_admin_mode_entered(db, user=user, until=until)
    return until


async def leave(sessions: SessionStore, *, token: str | None) -> None:
    """Clear the stamp. Idempotent, and never an error: it is a way out."""
    if token is not None:
        await sessions.set_admin_until(token, None)


def holds_the_platform(user: User, session: Session, now: datetime | None = None) -> None:
    """Refuse anyone who is not the operator, or is not freshly verified.

    Two refusals rather than one, because they are two different problems: the
    first is answered by signing in as somebody else, the second by confirming
    it is you. A caller told only "forbidden" would retry the wrong one.
    """
    if not is_the_operator(user):
        raise PermissionDeniedError(NOT_THE_OPERATOR)
    if not session.in_admin_mode(now or datetime.now(UTC)):
        raise PermissionDeniedError(NEEDS_RE_AUTHENTICATION)


async def _proved(
    db: AsyncSession,
    factors: SecondFactorStore,
    box: CredentialBox | None,
    *,
    user: User,
    password: str | None,
    code: str | None,
) -> bool:
    if code:
        return await verify_second_factor(db, factors, box, user=user, code=code)
    if second_factor.is_enabled(user):
        # Enrolled means the code is the proof; the password alone is the weaker
        # way in the operator chose to close (group 9, A3).
        return False
    if password:
        # Always runs the hasher, as signing in does, so a wrong password costs
        # what a right one costs.
        return verify_password(user.password_hash, password)
    return False
