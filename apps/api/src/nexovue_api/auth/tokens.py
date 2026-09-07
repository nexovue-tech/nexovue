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

Comparison is by hash lookup rather than by string equality: Redis finds the key
or does not, so there is no timing signal to leak in the first place. That is
the same reason `sessions.py` keys on a fingerprint.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from typing import Final

from redis.asyncio import Redis

from nexovue_api.auth.sessions import token_fingerprint

TOKEN_KEY_PREFIX: Final = "onetime"

# 256 bits, matching a session token. These are random rather than chosen, so
# there is nothing for key stretching to buy.
TOKEN_BYTES: Final = 32


class TokenPurpose(StrEnum):
    """What a token may be presented for, and nothing else.

    The value is part of the Redis key, so presenting a verification token to
    the reset endpoint finds no key at all.
    """

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"


class OneTimeTokenStore:
    """Issue a token, and spend it exactly once."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def issue(self, *, purpose: TokenPurpose, user_id: str, ttl_seconds: int) -> str:
        """Mint a token for one user and one purpose, and return it to be mailed.

        The caller supplies the lifetime because the two purposes do not share
        one: a verification link is followed at leisure, a reset link is a live
        credential to somebody's account.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._redis.set(_key(purpose, token), user_id, ex=ttl_seconds)
        return token

    async def consume(self, *, purpose: TokenPurpose, token: str) -> str | None:
        """The user id behind a token, spending it in the same round trip.

        `None` covers every way a token can fail — never issued, already spent,
        expired, or issued for the other purpose — deliberately, because the
        caller must answer all four identically. A caller that could tell
        "expired" from "never existed" would be telling an attacker which
        guesses were once real.
        """
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


def _key(purpose: TokenPurpose, token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}:{purpose.value}:{token_fingerprint(token)}"
