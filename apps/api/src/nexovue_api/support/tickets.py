"""The single-use pass that opens a support view.

The platform area writes a ticket here — 60 seconds, spent on first use — and
`/support/view-as` turns it into a read-only, time-bound session as the person
being viewed. Stored by its fingerprint, like every other token, on the
request's own transaction — a ticket is issued with the audit entry that names
it, and spent only by a request that went on to open the view.

Since ADR-018 both live on one origin under one cookie, so redeeming a ticket
*replaces* the operator's own session in that browser until the view ends. The
screen that issues it says so; a second cookie would be the other answer, and it
would rebuild the separation ADR-018 removed.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.sessions import token_fingerprint
from nexovue_api.data.postgres.models import ViewAsTicketRecord

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
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def issue(self, ticket: ViewAsTicket) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._db.execute(
            insert(ViewAsTicketRecord).values(
                token_hash=token_fingerprint(token),
                user_id=ticket.user_id,
                org_id=ticket.org_id,
                operator_label=ticket.operator_label,
                reason=ticket.reason,
                grant_id=ticket.grant_id,
                impersonation_id=ticket.impersonation_id,
                until=ticket.until,
                expires_at=datetime.now(UTC) + timedelta(seconds=TICKET_TTL_SECONDS),
            )
        )
        return token

    async def spend(self, token: str) -> ViewAsTicket | None:
        """The ticket, once; a second spend — or an expired one — is None."""
        return await self._spend_ticket_by_token_hash(token_fingerprint(token))

    async def _spend_ticket_by_token_hash(self, fingerprint: str) -> ViewAsTicket | None:
        """Found by the ticket alone: it is what establishes whose view this is."""
        row = await self._db.scalar(
            delete(ViewAsTicketRecord)
            .where(
                ViewAsTicketRecord.token_hash == fingerprint,
                ViewAsTicketRecord.expires_at > datetime.now(UTC),
            )
            .returning(ViewAsTicketRecord)
        )
        if row is None:
            return None
        return ViewAsTicket(
            user_id=row.user_id,
            org_id=row.org_id,
            operator_label=row.operator_label,
            reason=row.reason,
            grant_id=row.grant_id,
            impersonation_id=row.impersonation_id,
            until=row.until,
        )
