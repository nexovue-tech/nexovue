"""Changing your own account. No HTTP, no SQL text.

**One write ends every session, and it is the same write in both directions.**
Redis keys sessions on `session:<fingerprint(token)>` and `SessionStore.revoke`
takes a *token*, so nothing maps a user to their sessions and "end them all" has
no query to run. `users.sessions_valid_from` is the answer migration 0019 chose:
a session created before it is refused in `auth.dependencies`, so setting it to
`now()` invalidates every session in existence for that user in a single write.
That migration says in as many words that a *future* reason to end every session
should bump this same column rather than adding a second one, and both reasons
below are that future.

**The caller's own session survives a password change and does not survive a
sign-out-everywhere**, and the difference is not a preference:

* A password change is gated on the current password, which is exactly the thing
  a stolen session does not have. The caller has therefore proved they are the
  owner, on this device, seconds ago. Signing them out here buys nothing — they
  would sign straight back in with the password they just chose — while every
  *other* session, which is where an attacker would be sitting, is ended. So the
  epoch is bumped and a fresh session is minted for the caller alone.
* Sign-out-everywhere is what somebody clicks when they think they are already
  compromised, and "everywhere" has to include the machine in front of them —
  which may be the shared or borrowed one they are trying to get out of. Keeping
  the caller's session would make the control quieter than its own name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.credentials import hash_password, verify_password
from nexovue_api.auth.schemas import UserSummary
from nexovue_api.auth.service import describe_user, start_session
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profile.schemas import ProfileUpdate

# Deliberately the same wording whether the account has no password at all (an
# invited user, `users.password_hash` NULL) or the one supplied is wrong. Both
# are "you did not prove you hold the current password", and the caller's remedy
# in both cases is the reset link.
CURRENT_PASSWORD_WRONG: Final = "That is not the current password on this account."

# Refused rather than accepted as a no-op: it is almost always a mis-paste, and
# accepting it would end every other session for no change at all.
PASSWORD_UNCHANGED: Final = (
    "The new password is the same as the current one. Choose a different one."
)


def rename(user: User, update: ProfileUpdate) -> UserSummary:
    """Set the caller's display name and describe the account back.

    The row is already attached to the request's session, so the assignment is
    the write; the request dependency commits it.
    """
    user.name = _stored_name(update.name)
    return _describe(user)


async def change_password(
    db: AsyncSession,
    sessions: SessionStore,
    *,
    user: User,
    current_token: str | None,
    current_password: str,
    new_password: str,
) -> str:
    """Verify the current password, set the new one, and hand back a fresh token.

    Every session the caller had — including the one this request arrived on —
    is ended, and a single new one is minted for them. See the module docstring
    for why that asymmetry is the right one.
    """
    if not verify_password(user.password_hash, current_password):
        raise InvalidRequestError(CURRENT_PASSWORD_WRONG)
    # The current password has just been proved correct, so comparing the two
    # plaintexts answers "is this the same password" without a second argon2.
    if new_password == current_password:
        raise InvalidRequestError(PASSWORD_UNCHANGED)

    user.password_hash = hash_password(new_password)
    _end_every_session(user)
    audit.record_password_changed(db, user=user)

    # The old token is dead by the epoch already; deleting the key as well means
    # the store holds nothing that looks live for a session that is not.
    if current_token is not None:
        await sessions.revoke(current_token)
    return await start_session(db, sessions, user=user)


async def sign_out_everywhere(
    db: AsyncSession,
    sessions: SessionStore,
    *,
    user: User,
    current_token: str | None,
) -> None:
    """End every session this user has, this one included.

    No password is asked for. The caller is somebody who believes they are
    compromised, and a stolen session calling this can do nothing but sign
    itself out alongside the owner — an inconvenience, never an escalation.
    """
    _end_every_session(user)
    audit.record_all_sessions_ended(db, user=user)
    if current_token is not None:
        await sessions.revoke(current_token)


def _end_every_session(user: User) -> None:
    """Bump the epoch every authenticated request is already checked against."""
    user.sessions_valid_from = datetime.now(UTC)


def _stored_name(supplied: str | None) -> str | None:
    """The name as the column holds it: trimmed, and NULL when there is none."""
    trimmed = (supplied or "").strip()
    return trimmed or None


def _describe(user: User) -> UserSummary:
    """The same shape `/auth/me` answers with, so the screen learns one."""
    return describe_user(user)
