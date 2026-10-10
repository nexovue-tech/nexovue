"""One-time tokens for email verification, password reset and change of address.

**The same posture as `sessions.py`, for the same reasons.** The table holds a
hash of the token and never the token, so a dump of it is a list of expiry times
rather than a set of usable credentials; and a token is live only while
`expires_at` is ahead, which every read checks.

**Spent with the work it authorises.** The store runs on the request's own
transaction, so a reset whose new password is refused leaves its link unspent,
and a link is never spent by a request that then failed.

**Single use is enforced by the delete, not by a flag.** `consume` is one
`DELETE … RETURNING`. Two requests arriving together both try to delete the same
row; Postgres lets one have it and the other finds nothing, so a reset token
cannot be raced.

**The purpose is part of the predicate**, so a verification token cannot be
presented to the reset endpoint. Without that, one token type would be
interchangeable with the other and the weaker of the two would set the strength
of both.

**A change-of-address link carries the address, not just the account.** Two
purposes need only "whose account is this"; changing an address also needs "to
which one", and the answer rides with the token rather than on the user row. A
`users.pending_email` column would be overwritten by a second request, and
following the *first* link would then apply an address that link never proved —
control of the old inbox standing in as proof of a new one. Binding the address
to the token closes that: a link applies the address it was minted for or
nothing.

Comparison is by hash lookup rather than by string equality, so there is no
timing signal to leak in the first place. That is the same reason `sessions.py`
keys on a fingerprint.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.sessions import token_fingerprint
from nexovue_api.data.postgres.models import OneTimeToken

# 256 bits, matching a session token. These are random rather than chosen, so
# there is nothing for key stretching to buy.
TOKEN_BYTES: Final = 32


class TokenPurpose(StrEnum):
    """What a token may be presented for, and nothing else.

    The value is part of every lookup, so presenting a verification token to
    the reset endpoint finds nothing.
    """

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"
    CHANGE_EMAIL = "change_email"


# The purposes whose whole claim is "this account". `CHANGE_EMAIL` is not one:
# its claim names an address too, so it is issued and spent through its own pair
# of methods and a caller cannot reach it with a user id it invented.
ID_VALUED_PURPOSES: Final = frozenset({TokenPurpose.VERIFY_EMAIL, TokenPurpose.RESET_PASSWORD})


@dataclass(frozen=True, slots=True)
class EmailChange:
    """What a change-of-address link stands for: whose account, and which address."""

    user_id: str
    new_email: str


class OneTimeTokenStore:
    """Issue a token, and spend it exactly once."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def issue(self, *, purpose: TokenPurpose, user_id: str, ttl_seconds: int) -> str:
        """Mint a token for one user and one purpose, and return it to be mailed.

        The caller supplies the lifetime because the purposes do not share one:
        a verification link is followed at leisure, a reset link is a live
        credential to somebody's account.
        """
        return await self._mint(_id_valued(purpose), user_id, None, ttl_seconds)

    async def consume(self, *, purpose: TokenPurpose, token: str) -> str | None:
        """The user id behind a token, spending it in the same statement.

        `None` covers every way a token can fail — never issued, already spent,
        expired, or issued for another purpose — deliberately, because the
        caller must answer all of them identically. A caller that could tell
        "expired" from "never existed" would be telling an attacker which
        guesses were once real.
        """
        spent = await self._spend(_id_valued(purpose), token)
        return None if spent is None else str(spent.user_id)

    async def issue_email_change(self, *, user_id: str, new_email: str, ttl_seconds: int) -> str:
        """Mint a link that moves one account to one address, and no other."""
        return await self._mint(TokenPurpose.CHANGE_EMAIL, user_id, new_email, ttl_seconds)

    async def consume_email_change(self, *, token: str) -> EmailChange | None:
        """The account and address behind a change-of-address link, spending it."""
        spent = await self._spend(TokenPurpose.CHANGE_EMAIL, token)
        if spent is None or spent.new_email is None:
            return None
        return EmailChange(user_id=str(spent.user_id), new_email=spent.new_email)

    async def revoke_all_for(self, *, purpose: TokenPurpose, user_id: str) -> int:
        """Spend every outstanding token of one purpose for one user; how many there were."""
        result = await self._db.execute(
            delete(OneTimeToken).where(
                OneTimeToken.purpose == purpose.value,
                OneTimeToken.user_id == uuid.UUID(user_id),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def _mint(
        self, purpose: TokenPurpose, user_id: str, new_email: str | None, ttl_seconds: int
    ) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._db.execute(
            insert(OneTimeToken).values(
                token_hash=token_fingerprint(token),
                purpose=purpose.value,
                user_id=uuid.UUID(user_id),
                new_email=new_email,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            )
        )
        return token

    async def _spend(self, purpose: TokenPurpose, token: str) -> _Spent | None:
        """`DELETE … RETURNING`: read and remove in one statement, so two requests
        arriving together cannot both succeed."""
        if not token:
            return None
        row = (
            await self._db.execute(
                delete(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == token_fingerprint(token),
                    OneTimeToken.purpose == purpose.value,
                    OneTimeToken.expires_at > datetime.now(UTC),
                )
                .returning(OneTimeToken.user_id, OneTimeToken.new_email)
            )
        ).first()
        return None if row is None else _Spent(user_id=row.user_id, new_email=row.new_email)


@dataclass(frozen=True, slots=True)
class _Spent:
    user_id: uuid.UUID
    new_email: str | None


def _id_valued(purpose: TokenPurpose) -> TokenPurpose:
    """Refuse a purpose whose claim is more than a user id.

    A programming error, not a request: spending a change-of-address token
    through `consume` would hand the caller an address claim where it expected an
    id, and it should fail here rather than three frames later.
    """
    if purpose not in ID_VALUED_PURPOSES:
        raise ValueError(f"{purpose.value} carries more than a user id; use its own methods")
    return purpose
