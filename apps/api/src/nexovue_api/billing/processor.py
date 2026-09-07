"""The port every payment leaves and every billing event arrives through.

No processor is chosen yet. This interface exists first because the choice is
genuinely open and genuinely consequential: a domestic Indian gateway
(Cashfree, Razorpay) leaves us the legal seller and therefore liable for foreign
VAT registration, while a merchant of record becomes the seller and carries that
instead. Those are different companies, different compliance, and possibly both
— one for India and one for everywhere else.

**That is why this is a Protocol and not a client.** Two implementations chosen
per customer is a normal outcome here, not a compromise, and it is only cheap if
nothing above this file knows which one ran.

**The interface speaks our domain, never a processor's**, the same rule
`data/objectstore/store.py` follows for the S3 API. A `plan_id` that is really a
Paddle price id, a `cf_order_token`, a `subscription_schedule` — each is one
vendor's noun, and the first one to reach this module is the one that makes the
second implementation a rewrite.

**The webhook is the hard half and the reason `BillingEvent` exists.** Every
processor pushes events at us in its own shape, and the temptation is to hand
that JSON to the code that changes a plan. Then the plan-change logic knows
Paddle's field names, and the abstraction was decoration. An implementation
verifies the signature, translates the payload into one of the members below,
and nothing downstream ever sees the original.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class BillingError(Exception):
    """The processor refused, or could not be reached.

    Separate from a *declined payment*, which is not an error: a card that fails
    is an event the processor reports and this product acts on. This is for the
    call itself failing.
    """


class SignatureInvalidError(BillingError):
    """A webhook arrived that the processor did not sign.

    Its own type because the response differs: a caller must refuse it without
    reading the body, the way `deploys` already refuses an unsigned delivery.
    """


class BillingEventKind(Enum):
    """What a processor's webhook meant, in our words.

    A closed set. An event a processor sends that maps to none of these is one
    this product does not act on, and an implementation drops it rather than
    inventing a member — silently growing this enum from a vendor's changelog is
    how the translation layer stops being one.
    """

    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """One webhook, translated. The only shape billing logic ever sees."""

    kind: BillingEventKind
    org_id: uuid.UUID
    # The processor's own subscription identifier, stored so a later call can
    # name the same subscription. Opaque: nothing parses or branches on it.
    subscription_ref: str
    plan_slug: str
    # Unique per delivery, so a replayed webhook is detectable — the same
    # property `X-GitHub-Delivery` gives door 4a.
    delivery_id: str


@dataclass(frozen=True, slots=True)
class Checkout:
    """Where to send a customer to pay.

    A URL rather than a form, because the card details must never reach this
    application: what we hold is the processor's customer reference and nothing
    else avoidable, which is ADR 0076's posture applied to money.
    """

    url: str
    processor_customer_ref: str


class PaymentProcessor(Protocol):
    """The only way money is taken, and the only way billing events arrive."""

    async def begin_checkout(
        self, *, org_id: uuid.UUID, plan_slug: str, billing_email: str
    ) -> Checkout:
        """Start a subscription, and hand back where to send the customer.

        `plan_slug` is ours — `pro`, `scale` — matching `plan_prices`. Mapping it
        to whatever the processor calls a price is the implementation's job and
        the only place that mapping may live.
        """
        ...

    async def cancel(self, *, subscription_ref: str) -> None:
        """End a subscription. Idempotent: cancelling a cancelled one is not an
        error, because a customer pressing twice must not see a failure."""
        ...

    def event_from_webhook(self, *, body: bytes, headers: dict[str, str]) -> BillingEvent:
        """Verify a webhook's signature and translate it into our vocabulary.

        Takes the **raw body**, not a parsed object: a signature is computed over
        the bytes that arrived, and re-serializing would verify something the
        sender never sent. Door 4a's webhook already reads the raw stream for
        exactly this reason.

        Raises `SignatureInvalidError` before doing anything else with the body.
        """
        ...
