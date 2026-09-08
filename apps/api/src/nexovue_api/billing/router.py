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

from typing import Final

from fastapi import APIRouter, Request, status

from nexovue_api.billing import service
from nexovue_api.billing.dependencies import DomesticPayments
from nexovue_api.billing.schemas import DeliveryAcknowledgement
from nexovue_api.deps import DbSession
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
