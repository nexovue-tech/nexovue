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

**Ending *one* session is a different mechanism from ending them all, and has
to be.** The epoch above is a blunt instrument by design — it refuses everything
older than a moment — so it cannot express "this one and not that one". A single
session ends by deleting its key, which is what `SessionStore.revoke_one_of`
does, and the id it takes is proved to be in the caller's own index before
anything is deleted. The list the customer picks from comes from that same
index, so the set they can see and the set they can end are the same set.

**A change of address starts here and finishes somewhere else.** Asking for one
is a profile action gated on the current password; completing one is a proof of
control over an inbox, followed from a mail client with no session — so it lives
with the other link-following flows in `auth/recovery.py`. Nothing on the
account changes until that link is followed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from nexovue_platform.email import EmailSender
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import recovery
from nexovue_api.auth.credentials import CURRENT_PASSWORD_WRONG, hash_password, verify_password
from nexovue_api.auth.schemas import UserSummary
from nexovue_api.auth.service import describe_user, start_session
from nexovue_api.auth.sessions import SessionStore, token_fingerprint
from nexovue_api.auth.tokens import OneTimeTokenStore
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.profile.schemas import ProfileUpdate, SessionSummary
from nexovue_api.profile.timezones import stored_timezone

# `CURRENT_PASSWORD_WRONG` is imported from `auth.credentials` rather than
# written here: arming a second factor gives the same refusal, and one wording
# for one gate is one thing to keep true.

# Refused rather than accepted as a no-op: it is almost always a mis-paste, and
# accepting it would end every other session for no change at all.
PASSWORD_UNCHANGED: Final = (
    "The new password is the same as the current one. Choose a different one."
)


def update_details(user: User, update: ProfileUpdate) -> UserSummary:
    """Set the caller's display name and time zone, and describe the account back.

    The row is already attached to the request's session, so the assignments are
    the write; the request dependency commits it.

    The time zone is judged before either field is assigned, so a bad zone name
    does not leave a new name behind on a request that was refused.
    """
    timezone = stored_timezone(update.timezone)
    user.name = _stored_name(update.name)
    user.timezone = timezone
    return _describe(user)


async def request_email_change(
    db: AsyncSession,
    tokens: OneTimeTokenStore,
    email: EmailSender,
    *,
    user: User,
    current_password: str,
    new_address: str,
    app_base_url: str,
    ttl_seconds: int,
) -> None:
    """Prove the caller owns this account, then mail the new address a link.

    **The gate is the current password, for a stronger reason than the password
    change has.** An address is the account's recovery channel: whoever receives
    mail at it can reset the password and take the account outright. So a change
    of address is the *first* move in a takeover, and a stolen session — which
    can read every screen but cannot produce the password — is exactly the
    caller this refuses.

    An invited user with no password (F26, `password_hash` NULL) cannot change
    their address here, and the refusal is worded identically to a wrong
    password so the endpoint never says which case it is. Their route is the
    reset link, the same as for a password change.

    Nothing is written. `recovery.send_email_change_link` owns what happens next
    and the module docstring there says why the account stays as it is.
    """
    if not verify_password(user.password_hash, current_password):
        raise InvalidRequestError(CURRENT_PASSWORD_WRONG)
    await recovery.send_email_change_link(
        db,
        tokens,
        email,
        user=user,
        new_address=new_address,
        app_base_url=app_base_url,
        ttl_seconds=ttl_seconds,
    )


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


async def list_sessions(
    sessions: SessionStore, *, user: User, current_token: str | None
) -> list[SessionSummary]:
    """Every session live for this user, with the caller's own marked.

    **Scoped by construction rather than by a filter somebody remembered.** The
    store is asked for one user's index and that user is the one the session
    named; there is no argument a request could carry that would point this at
    anybody else, and no path where an id is looked up before it has been shown
    to belong to the caller.

    `current` is computed from the request's own token, never sent by the
    client: a caller that could nominate which row is "this one" could talk a
    customer out of ending the session that is not theirs.
    """
    here = token_fingerprint(current_token) if current_token else None
    return [
        SessionSummary(
            id=session.id,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            client=session.client.label,
            network=session.client.network,
            current=session.id == here,
        )
        for session in await sessions.list_for(str(user.id))
    ]


async def end_one_session(
    db: AsyncSession, sessions: SessionStore, *, user: User, session_id: str
) -> bool:
    """End a single session of the caller's, and say whether it was theirs.

    **No password is asked for**, on the same reasoning `sign_out_everywhere`
    gives: the caller is somebody who thinks a session is not theirs, and a
    stolen session calling this can do nothing worse than sign somebody out.

    Returns False for an id that is not in this user's index — whether it
    belongs to somebody else or to nobody at all. The router answers both with
    one 404, so this cannot be used to learn that another account's session is
    live.
    """
    ended = await sessions.revoke_one_of(user_id=str(user.id), session_id=session_id)
    if ended:
        audit.record_session_ended(db, org_id=user.org_id, user_id=user.id)
    return ended


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
