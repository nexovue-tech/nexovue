"""A person with no password, and the one-time link that sets one.

One primitive, two callers: an invited teammate (F26) and, later, the first owner
of an organization nexovue onboards by hand (F31, ADR-008 step 7). Either way
the person proves they control the address by following a link sent to it, and
chooses a password nobody else has seen.

**The link lives on the `users` row, as its SHA-256.** Not in `one_time_tokens`:
a pending invite is listed with its expiry from this row, and a resend kills the
link it replaces in the same write. The plaintext is only ever in the email.

**A refusal says as little as it can.** A link that never existed, was spent,
withdrawn or replaced all get one message. Only *expired* is told apart, because
the person can do something about it — ask for a new one — and a link that
expired was, by definition, once sent to them.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth import queries
from nexovue_api.auth.credentials import hash_password
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.errors.exceptions import InvalidRequestError

# 256 bits; `token_urlsafe` makes 43 characters of it, which survive a mail client.
LINK_BYTES: Final = 32

LINK_NO_LONGER_VALID: Final = (
    "This link is no longer valid. Ask whoever invited you to send a new one."
)


@dataclass(frozen=True, slots=True)
class LinkHolder:
    """Who a live link belongs to, and who sent it."""

    user: User
    organization: Organization
    invited_by: User | None


def hash_link(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(user: User, *, now: datetime, lifetime: timedelta) -> str:
    """Give `user` a fresh link, replacing any they had, and return its plaintext."""
    token = secrets.token_urlsafe(LINK_BYTES)
    user.set_password_token_hash = hash_link(token)
    user.set_password_expires_at = now + lifetime
    return token


def clear(user: User) -> None:
    user.set_password_token_hash = None
    user.set_password_expires_at = None


async def holder_of(db: AsyncSession, token: str, *, now: datetime) -> LinkHolder:
    """The person a live link names, or the refusal a person can act on."""
    found = await queries.find_by_set_password_link(db, hash_link(token))
    if found is None:
        raise InvalidRequestError(LINK_NO_LONGER_VALID)
    user, organization, invited_by = found
    expires_at = user.set_password_expires_at
    if expires_at is not None and expires_at <= now:
        raise InvalidRequestError(_expired(expires_at, invited_by))
    return LinkHolder(user=user, organization=organization, invited_by=invited_by)


async def set_password(
    db: AsyncSession, token: str, *, name: str, password: str, now: datetime
) -> LinkHolder:
    """Spend the link: the person now has a name, a password and a proven address."""
    holder = await holder_of(db, token, now=now)
    user = holder.user
    user.name = name.strip()
    user.password_hash = hash_password(password)
    if user.email_verified_at is None:
        # Following a link sent to the address proves it, as a reset does.
        user.email_verified_at = now
    clear(user)
    return holder


def display_name(user: User | None) -> str:
    # No inviter means nexovue onboarded this owner by hand (F31, ADR-008).
    if user is None:
        return "nexovue"
    return user.name or user.email


def _expired(expires_at: datetime, invited_by: User | None) -> str:
    return (
        f"This link expired on {expires_at:%d %B %Y}. "
        f"Ask {display_name(invited_by)} to send a new one."
    )
