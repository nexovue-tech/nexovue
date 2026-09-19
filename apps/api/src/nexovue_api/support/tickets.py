"""The single-use pass that carries staff from the admin host to the customer host.

A staff session and a customer session never share a cookie or an origin
(ADR-016). Instead the admin app writes a ticket here — 60 seconds, spent on
first use — and the customer app turns it into a read-only, time-bound customer
session. Stored by its fingerprint, like every other token in Redis.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from redis.asyncio import Redis

from nexovue_api.auth.sessions import token_fingerprint

KEY_PREFIX: Final = "view-as-ticket"
TICKET_TTL_SECONDS: Final = 60
TOKEN_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class ViewAsTicket:
    user_id: uuid.UUID
    org_id: uuid.UUID
    staff_label: str
    reason: str
    grant_id: uuid.UUID
    impersonation_id: uuid.UUID
    until: datetime


class TicketStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def issue(self, ticket: ViewAsTicket) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        payload = {
            "user_id": str(ticket.user_id),
            "org_id": str(ticket.org_id),
            "staff": ticket.staff_label,
            "reason": ticket.reason,
            "grant": str(ticket.grant_id),
            "impersonation": str(ticket.impersonation_id),
            "until": ticket.until.isoformat(),
        }
        await self._redis.set(_key(token), json.dumps(payload), ex=TICKET_TTL_SECONDS)
        return token

    async def spend(self, token: str) -> ViewAsTicket | None:
        """The ticket, once; a second spend — or an expired one — is None."""
        raw = await self._redis.getdel(_key(token))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return ViewAsTicket(
                user_id=uuid.UUID(payload["user_id"]),
                org_id=uuid.UUID(payload["org_id"]),
                staff_label=str(payload["staff"]),
                reason=str(payload["reason"]),
                grant_id=uuid.UUID(payload["grant"]),
                impersonation_id=uuid.UUID(payload["impersonation"]),
                until=datetime.fromisoformat(payload["until"]),
            )
        except (ValueError, KeyError, TypeError):
            return None


def _key(token: str) -> str:
    return f"{KEY_PREFIX}:{token_fingerprint(token)}"
