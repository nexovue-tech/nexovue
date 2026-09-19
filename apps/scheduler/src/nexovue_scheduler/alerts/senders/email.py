"""The `email` and `owners` channels, through the shared mail port.

`owners` is resolved when the message is sent, not when the rule was written:
the organization's owners with a verified address today, so a changed address or
(after F26) a second owner is right without anyone editing a rule (FR2).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Final

from nexovue_platform.email import EmailError, EmailSender, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.alerts.messages import AlertMessage
from nexovue_scheduler.alerts.outbox import Channel, SendResult

_VERIFIED_OWNERS: Final = text(
    """
    SELECT email FROM users
    WHERE org_id = :org_id AND role = 'owner' AND email_verified_at IS NOT NULL
      AND removed_at IS NULL
    ORDER BY email
    """
)

NOT_CONFIGURED: Final = (
    "email is not configured on this deployment — set EMAIL_PROVIDER and the other "
    "EMAIL_* variables for the scheduler"
)
NO_VERIFIED_OWNER: Final = "no owner of this organization has a verified email address yet"
NO_ADDRESSES: Final = "the channel has no addresses"


async def recipients(connection: AsyncConnection, channel: Channel) -> list[str]:
    if channel.kind == "owners":
        return await verified_owners(connection, channel.org_id)
    return [str(address) for address in channel.config.get("addresses", [])]


async def verified_owners(connection: AsyncConnection, org_id: uuid.UUID) -> list[str]:
    rows = await connection.execute(_VERIFIED_OWNERS, {"org_id": org_id})
    return [row.email for row in rows]


async def send(
    sender: EmailSender | None,
    addresses: list[str],
    message: AlertMessage,
    *,
    message_id: str,
    fallback_manage_link: str,
    owners: bool,
) -> SendResult:
    if sender is None:
        return SendResult(outcome="failed", reason=NOT_CONFIGURED)
    if not addresses:
        return SendResult(outcome="failed", reason=NO_VERIFIED_OWNER if owners else NO_ADDRESSES)

    data = {
        "subject": message.summary,
        "body": "\n".join(message.details),
        "link": message.link,
        "rule_name": message.rule_name,
        "manage_link": message.manage_link or fallback_manage_link,
    }
    for address in addresses:
        try:
            await sender.send(
                to=address,
                message=Message.ALERT,
                data=data,
                idempotency_key=_idempotency_key(message_id, address),
            )
        except EmailError as failure:
            # The provider could not be reached or refused. Retried: a refusal that
            # retrying cannot fix ends at the hour, with this reason on record.
            return SendResult(outcome="retry", reason=str(failure))
    return SendResult(outcome="delivered")


def _idempotency_key(message_id: str, address: str) -> str:
    """The same on every retry of one batch to one address, so an address the first
    attempt reached is not mailed again. Hashed: an address can be 254 characters and
    the provider takes 256 in all."""
    return f"alert-{message_id}-{hashlib.sha256(address.encode()).hexdigest()[:32]}"
