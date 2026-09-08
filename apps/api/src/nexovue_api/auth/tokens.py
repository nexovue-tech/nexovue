"""One-time tokens for email verification and password reset, in Redis.

**The same posture as `sessions.py`, for the same reasons.** The store holds a
hash of the token and never the token, so a dump of Redis is a list of expiry
times rather than a set of usable credentials; and expiry is the key's own TTL
rather than a timestamp somebody compares, because *"a store that forgets on its
own cannot accumulate live sessions nobody cleans up"* — which is equally true of
reset links, and is why these are not a Postgres table with a purge obligation
nobody has written.

**Single use is enforced by the read, not by a flag.** `consume` is `GETDEL`:
one round trip that returns the value and removes the key atomically. A
read-then-delete pair would let two requests arriving together both succeed,
which for a reset token means an attacker racing the customer.

**The purpose is part of the key**, so a verification token cannot be presented
to the reset endpoint. Without that, one token type would be interchangeable
with the other and the weaker of the two would set the strength of both.

**A change-of-address link carries the address, not just the account.** Two
purposes need only "whose account is this"; changing an address also needs "to
which one", and the answer rides in the token rather than on the user row. A
`users.pending_email` column would be overwritten by a second request, and
following the *first* link would then apply an address that link never proved —
control of the old inbox standing in as proof of a new one. Binding the address
to the token closes that: a link applies the address it was minted for or
nothing.

Comparison is by hash lookup rather than by string equality: Redis finds the key
or does not, so there is no timing signal to leak in the first place. That is
the same reason `sessions.py` keys on a fingerprint.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from redis.asyncio import Redis

from nexovue_api.auth.sessions import token_fingerprint

TOKEN_KEY_PREFIX: Final = "onetime"

# 256 bits, matching a session token. These are random rather than chosen, so
# there is nothing for key stretching to buy.
TOKEN_BYTES: Final = 32

_USER_ID_FIELD: Final = "user_id"
_NEW_EMAIL_FIELD: Final = "new_email"


class TokenPurpose(StrEnum):
    """What a token may be presented for, and nothing else.

    The value is part of the Redis key, so presenting a verification token to
    the reset endpoint finds no key at all.
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

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def issue(self, *, purpose: TokenPurpose, user_id: str, ttl_seconds: int) -> str:
        """Mint a token for one user and one purpose, and return it to be mailed.

        The caller supplies the lifetime because the purposes do not share one:
        a verification link is followed at leisure, a reset link is a live
        credential to somebody's account.
        """
        return await self._mint(_id_valued(purpose), user_id, ttl_seconds)

    async def consume(self, *, purpose: TokenPurpose, token: str) -> str | None:
        """The user id behind a token, spending it in the same round trip.

        `None` covers every way a token can fail — never issued, already spent,
        expired, or issued for another purpose — deliberately, because the
        caller must answer all of them identically. A caller that could tell
        "expired" from "never existed" would be telling an attacker which
        guesses were once real.
        """
        return await self._spend(_id_valued(purpose), token)

    async def issue_email_change(self, *, user_id: str, new_email: str, ttl_seconds: int) -> str:
        """Mint a link that moves one account to one address, and no other."""
        claim = json.dumps({_USER_ID_FIELD: user_id, _NEW_EMAIL_FIELD: new_email})
        return await self._mint(TokenPurpose.CHANGE_EMAIL, claim, ttl_seconds)

    async def consume_email_change(self, *, token: str) -> EmailChange | None:
        """The account and address behind a change-of-address link, spending it.

        `None` for every failure, including a stored value that will not decode:
        the only way to hold one of those is to have written it into Redis by
        hand, and a caller that crashed on it would be a caller that can be made
        to crash from outside the application.
        """
        claim = await self._spend(TokenPurpose.CHANGE_EMAIL, token)
        return None if claim is None else _decoded(claim)

    async def _mint(self, purpose: TokenPurpose, value: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._redis.set(_key(purpose, token), value, ex=ttl_seconds)
        return token

    async def _spend(self, purpose: TokenPurpose, token: str) -> str | None:
        """`GETDEL`: read and remove in one round trip, so two requests arriving
        together cannot both succeed."""
        if not token:
            return None
        spent = await self._redis.getdel(_key(purpose, token))
        if spent is None:
            return None
        return spent.decode() if isinstance(spent, bytes) else str(spent)

    async def revoke_all_for(self, *, purpose: TokenPurpose, user_id: str) -> None:
        """Not implemented, and deliberately not.

        Redis is keyed by the token's fingerprint, so finding every token for a
        user means scanning the keyspace — the same reason `sessions.py` cannot
        revoke a user's sessions and `users.sessions_valid_from` exists instead.
        Outstanding tokens are left to expire; the short TTL is what bounds them.
        """
        raise NotImplementedError(
            "tokens are bounded by their TTL, not by enumeration — see the docstring"
        )


def _id_valued(purpose: TokenPurpose) -> TokenPurpose:
    """Refuse a purpose whose claim is more than a user id.

    A programming error, not a request: spending a change-of-address token
    through `consume` would hand the caller a JSON document where it expected an
    id, and it should fail here rather than three frames later.
    """
    if purpose not in ID_VALUED_PURPOSES:
        raise ValueError(f"{purpose.value} carries more than a user id; use its own methods")
    return purpose


def _decoded(claim: str) -> EmailChange | None:
    """A stored change-of-address claim, or None if it is not one."""
    try:
        fields = json.loads(claim)
    except ValueError:
        return None
    if not isinstance(fields, dict):
        return None
    user_id = fields.get(_USER_ID_FIELD)
    new_email = fields.get(_NEW_EMAIL_FIELD)
    if not isinstance(user_id, str) or not isinstance(new_email, str):
        return None
    return EmailChange(user_id=user_id, new_email=new_email)


def _key(purpose: TokenPurpose, token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}:{purpose.value}:{token_fingerprint(token)}"
