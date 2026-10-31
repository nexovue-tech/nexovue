"""Sending one message to one channel, whatever its kind.

The outbox and *send test* both come here, so a test goes through exactly the path
an alert does (FR3). The channel's secret is opened here, in this process,
immediately before the request that uses it, and never leaves it (ADR-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nexovue_platform.alerts.channels import PAGERDUTY_ENDPOINTS, webhook_signature
from nexovue_platform.checks.sealing import SealedBox, SealError, channel_secret_owner
from nexovue_platform.email import EmailSender
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import payloads
from nexovue_scheduler.alerts.messages import AlertMessage
from nexovue_scheduler.alerts.outbox import Channel, SendResult
from nexovue_scheduler.alerts.senders import email, http

NO_KEY: Final = (
    "this deployment cannot open channel secrets — CHECK_SECRET_KEY is not set for the scheduler"
)
UNOPENABLE: Final = "the channel's secret could not be opened; replace it"
NO_SECRET: Final = "the channel has no secret; set one"
NO_SENDER: Final = "nexovue cannot deliver to this kind of channel"


@dataclass(frozen=True, slots=True)
class Outbound:
    """What sending needs from the process: its mail port, its secret box, where the
    console is, and whether a development stack may reach a private address."""

    email_sender: EmailSender | None
    secret_box: SealedBox | None
    app_base_url: str
    private_allowed: bool = False


@dataclass(frozen=True, slots=True)
class Outgoing:
    """One message and the identities its receiver keys on."""

    message: AlertMessage
    # The webhook's `webhook-id`: the same on every retry of one delivery (FR4).
    message_id: str
    # PagerDuty's `dedup_key`: `incident:<id>` or `finding:<id>`, so a subject's whole
    # life is one alert (AC20, ADR-022); for a subject noted on a linked one's alert,
    # that one's key (D29, D35).
    dedup_key: str
    # The subject's kind and id, which a webhook receiver keys its record on.
    subject_kind: str
    subject_id: str
    # The subject's name for people: telemetry text when it is a service.
    subject: str
    opened_at: datetime
    resolved_at: datetime | None
    # D29, D35: this trigger adds to an alert a linked finding or incident opened.
    noted: bool = False


async def send(
    engine: AsyncEngine, outbound: Outbound, channel: Channel, outgoing: Outgoing, *, now: datetime
) -> SendResult:
    kind = channel.kind
    if kind in ("email", "owners"):
        async with engine.connect() as connection:
            addresses = await email.recipients(connection, channel)
        return await email.send(
            outbound.email_sender,
            addresses,
            outgoing.message,
            message_id=outgoing.message_id,
            fallback_manage_link=f"{outbound.app_base_url.rstrip('/')}/alerts",
            owners=kind == "owners",
        )

    secret, refusal = _open(outbound, channel)
    if refusal is not None:
        return refusal
    assert secret is not None  # _open returns one or the other
    private = outbound.private_allowed
    message = outgoing.message

    if kind == "slack":
        return http.post(secret, payloads.slack(message), private_allowed=private, secret=secret)
    if kind == "discord":
        return http.post(secret, payloads.discord(message), private_allowed=private, secret=secret)
    if kind == "teams":
        return http.post(secret, payloads.teams(message), private_allowed=private, secret=secret)
    if kind == "pagerduty":
        region = str(channel.config.get("region", "us"))
        body = payloads.pagerduty(
            message,
            routing_key=secret,
            dedup_key=outgoing.dedup_key,
            subject=outgoing.subject,
            app_base_url=outbound.app_base_url,
            noted=outgoing.noted,
        )
        endpoint = PAGERDUTY_ENDPOINTS.get(region, PAGERDUTY_ENDPOINTS["us"])
        return http.post(endpoint, body, private_allowed=private, secret=secret)
    if kind == "webhook":
        url = str(channel.config.get("url", ""))
        body = payloads.webhook(
            message,
            subject_kind=outgoing.subject_kind,
            subject_id=outgoing.subject_id,
            subject=outgoing.subject,
            opened_at=outgoing.opened_at,
            resolved_at=outgoing.resolved_at,
            sent_at=now,
        )
        timestamp = int(now.timestamp())
        headers = [
            ("webhook-id", outgoing.message_id),
            ("webhook-timestamp", str(timestamp)),
            ("webhook-signature", webhook_signature(secret, outgoing.message_id, timestamp, body)),
        ]
        return http.post(url, body, headers=headers, private_allowed=private, secret=secret)
    return SendResult(outcome="failed", reason=NO_SENDER)


def _open(outbound: Outbound, channel: Channel) -> tuple[str | None, SendResult | None]:
    if channel.sealed_secret is None:
        return None, SendResult(outcome="failed", reason=NO_SECRET)
    if outbound.secret_box is None:
        return None, SendResult(outcome="failed", reason=NO_KEY)
    try:
        return (
            outbound.secret_box.open(
                channel.sealed_secret,
                owner=channel_secret_owner(str(channel.org_id), str(channel.id)),
            ),
            None,
        )
    except SealError:
        return None, SendResult(outcome="failed", reason=UNOPENABLE)
