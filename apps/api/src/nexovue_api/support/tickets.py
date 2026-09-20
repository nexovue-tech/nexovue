"""The single-use pass that opens a support view.

The platform area writes a ticket here — 60 seconds, spent on first use — and
`/support/view-as` turns it into a read-only, time-bound session as the person
being viewed. Stored by its fingerprint, like every other token in Redis.

Since ADR-018 both live on one origin under one cookie, so redeeming a ticket
*replaces* the operator's own session in that browser until the view ends. The
screen that issues it says so; a second cookie would be the other answer, and it
would rebuild the separation ADR-018 removed.
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
    operator_label: str
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
            "operator": ticket.operator_label,
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
                operator_label=str(payload["operator"]),
                reason=str(payload["reason"]),
                grant_id=uuid.UUID(payload["grant"]),
                impersonation_id=uuid.UUID(payload["impersonation"]),
                until=datetime.fromisoformat(payload["until"]),
            )
        except (ValueError, KeyError, TypeError):
            return None


def _key(token: str) -> str:
    return f"{KEY_PREFIX}:{token_fingerprint(token)}"
