"""Whose allowance a question ticket spends (F4 design, trap 1; plan T2.4).

The limiter runs before authentication, so it has only the bearer value. A ticket pays from
the person's and the organization's own allowance, as the person would: chat cannot be a
way round a person's limit, and many people's questions are not one address. One indexed
read finds the asking person, remembered briefly; the answer **never decides access** (the
reader does, on every read), and a ticket that is not ours has no owner and is counted
against its address, then refused by authentication.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.data.postgres.models import AssistantMessage

# A question lives minutes; this only spares Postgres a read per tool call.
REMEMBER_SECONDS: Final = 60
MAX_REMEMBERED: Final = 10_000


@dataclass(frozen=True, slots=True)
class TicketOwner:
    user_id: str
    org_id: str


class TicketOwners:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._known: OrderedDict[str, tuple[float, TicketOwner | None]] = OrderedDict()

    async def owner_of(self, presented: str) -> TicketOwner | None:
        # The ticket's SHA-256, as `assistant/tickets.py` stores it; never the ticket.
        ticket_hash = hashlib.sha256(presented.encode()).hexdigest()
        now = time.monotonic()
        remembered = self._known.pop(ticket_hash, None)
        if remembered is not None and now - remembered[0] < REMEMBER_SECONDS:
            self._known[ticket_hash] = remembered
            return remembered[1]
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(AssistantMessage.user_id, AssistantMessage.org_id).where(
                        AssistantMessage.ticket_hash == ticket_hash
                    )
                )
            ).first()
        owner = None if row is None else TicketOwner(str(row.user_id), str(row.org_id))
        self._known[ticket_hash] = (now, owner)
        while len(self._known) > MAX_REMEMBERED:
            self._known.popitem(last=False)
        return owner
