"""Billing: one public door, and nothing else yet.

``POST /billing/webhook/domestic`` is unauthenticated, like the deploy webhook,
and for the same reason: the sender is a payment processor and has no session.
**The signature is the credential**, so two things shape this module:

* the body is read as **raw bytes**, never as a parsed model — a signature
  covers the bytes that arrived, and a pydantic body parameter would have
  FastAPI answer 422 on a forged request before the signature was looked at;
* the read is bounded before it is buffered, because an unauthenticated caller
  chooses how much it sends.

**The path names the role, not the vendor.** ADR 0102 expects a second processor
— a merchant of record for customers outside India — and it will need its own
URL, registered in its own dashboard. A URL is pasted into somebody else's
configuration and is expensive to change afterwards, which is exactly the kind
of decision worth getting right while it is still free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from fastapi import APIRouter, Request, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayCommitSpend
from nexovue_api.billing import service
from nexovue_api.billing.dependencies import DomesticPayments, OptionalDomesticPayments
from nexovue_api.billing.schemas import (
    BillingOverview,
    CheckoutHandoff,
    DeliveryAcknowledgement,
    StartCheckoutRequest,
)
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.errors.exceptions import InvalidRequestError

router = APIRouter(prefix="/billing", tags=["billing"])

# A billing event is a few hundred bytes of JSON. Anything larger is not one,
# and buffering it to find that out would be the attack rather than the defence.
MAX_BODY_BYTES: Final = 64 * 1024

BODY_TOO_LARGE_DETAIL: Final = f"A billing event must be at most {MAX_BODY_BYTES} bytes."


@router.post(
    "/webhook/domestic",
    status_code=status.HTTP_200_OK,
    summary="Accept a billing event signed by the domestic payment processor",
)
async def receive_billing_event(
    db: DbSession,
    request: Request,
    payments: DomesticPayments,
) -> DeliveryAcknowledgement:
    return await service.record_from_webhook(
        db,
        configured=payments,
        body=await _bounded_body(request),
        # The whole header set, because which ones carry the signature, the
        # timestamp and the delivery id is the processor's business and
        # therefore the adapter's. Nothing here reads one.
        headers=dict(request.headers),
    )


async def _bounded_body(request: Request) -> bytes:
    """The raw bytes, refusing an oversized body without holding it in memory.

    Streamed rather than `await request.body()`: Content-Length is a value the
    client chose, so the cap has to be enforced on what actually arrives.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY_BYTES:
            raise InvalidRequestError(BODY_TOO_LARGE_DETAIL)
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("", summary="The plan, the subscription behind it, and the month's allowance")
async def read_billing_overview(
    identity: CurrentIdentity,
    db: DbSession,
    payments: OptionalDomesticPayments,
    # The currency to quote in. Omitted means the one this organization already
    # pays in, or the default before it has paid at all — never inferred from
    # where the request came from.
    currency: str | None = None,
) -> BillingOverview:
    """P1-75. Readable by any member: what the organization pays and how close
    it is to the ceiling is not administration, and a member who cannot see it
    finds out that ingestion stopped by noticing the telemetry went quiet."""
    return await service.overview(
        db,
        organization=identity.organization,
        today=datetime.now(UTC).date(),
        configured=payments,
        currency=currency,
    )


@router.post(
    "/checkout",
    status_code=status.HTTP_201_CREATED,
    summary="Start the mandate that moves this organization onto a paid plan",
)
async def start_checkout(
    identity: MayCommitSpend,
    db: DbSession,
    settings: AppSettings,
    payments: DomesticPayments,
    request: StartCheckoutRequest,
) -> CheckoutHandoff:
    """P1-74's upgrade half. Answers with somewhere to authorise a mandate and
    changes no plan — `organizations.plan` moves on the processor's event."""
    return await service.begin_plan_change(
        db,
        configured=payments,
        organization=identity.organization,
        request=request,
        billing_email=identity.user.email,
        app_base_url=settings.app_base_url,
        today=datetime.now(UTC).date(),
    )


@router.delete(
    "/subscription",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the processor to end this organization's subscription",
)
async def cancel_subscription(
    identity: MayCommitSpend,
    db: DbSession,
    payments: DomesticPayments,
) -> None:
    """P1-74's downgrade half. **202, not 204**: the arrangement ends when the
    processor says it has, at its own period boundary, and the plan moves on
    that event — so this has accepted the request rather than completed it."""
    await service.end_subscription(db, configured=payments, organization=identity.organization)
