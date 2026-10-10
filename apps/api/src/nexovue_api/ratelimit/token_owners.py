"""Which organization an API token belongs to, for the token tier's ceiling.

The limiter runs before authentication, so it has only the bearer value. One
indexed read finds the owner, and the answer is remembered for a minute so a
script's steady stream of requests costs Postgres one read a minute, not one
each. A token revoked within that minute is still refused by authentication;
all the minute can cost is that its last requests count toward its old owner.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.apitokens import queries
from nexovue_api.apitokens.tokens import hash_api_token

REMEMBER_SECONDS: Final = 60
# Past this the least recently used answer is forgotten, so invented tokens
# cost memory once rather than without bound.
MAX_REMEMBERED: Final = 10_000


class TokenOwners:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._known: OrderedDict[str, tuple[float, str | None]] = OrderedDict()

    async def organization_of(self, presented: str) -> str | None:
        """The owning organization's id, or None for a token that is not ours."""
        token_hash = hash_api_token(presented)
        now = time.monotonic()
        remembered = self._known.pop(token_hash, None)
        if remembered is not None and now - remembered[0] < REMEMBER_SECONDS:
            self._known[token_hash] = remembered
            return remembered[1]

        async with self._session_factory() as db:
            token = await queries.find_by_hash(db, token_hash)
        owner = None if token is None else str(token.org_id)
        self._known[token_hash] = (now, owner)
        while len(self._known) > MAX_REMEMBERED:
            self._known.popitem(last=False)
        return owner
