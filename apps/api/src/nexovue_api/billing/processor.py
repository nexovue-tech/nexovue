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
from enum import Enum, StrEnum
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


class ContactField(StrEnum):
    """A detail a processor needs about the person paying.

    **This exists so the port fixes no field of its own**, and the guard that
    caught the first draft of this docstring is why it is worded this way: naming
    the processors that differ would have put their names in the one file that
    must not carry them.

    Processors disagree about who collects these. Some hosted pages ask the payer
    directly and need nothing up front; at least one requires them on the create
    call and collects none itself. Fixing `phone` onto `begin_checkout` would
    write that one's requirement — itself downstream of one country's regulation
    — into a contract ADR 0102 built to outlive vendors, so the requirement is
    declared by the implementation instead. See ADR 0109.
    """

    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"


@dataclass(frozen=True, slots=True)
class BillingContact:
    """Who is paying, as far as any processor needs to know.

    Every field but the address is optional, because what is required is the
    processor's answer rather than this module's: a screen asks for exactly what
    `required_contact_fields` names, and an adapter that needs more than it
    declared is the defect this shape makes visible.

    **Not stored.** ADR 0109: the processor holds what it needs for the bank's
    pre-debit notices, and a copy of ours would be personal data kept for the
    convenience of not retyping.
    """

    email: str
    name: str | None = None
    phone: str | None = None

    def missing(self, required: frozenset[ContactField]) -> frozenset[ContactField]:
        """What the caller still owes, so a screen can ask before the network does."""
        held = {
            ContactField.EMAIL: self.email,
            ContactField.NAME: self.name,
            ContactField.PHONE: self.phone,
        }
        return frozenset(field for field in required if not held[field])


class PaymentProcessor(Protocol):
    """The only way money is taken, and the only way billing events arrive."""

    def required_contact_fields(self) -> frozenset[ContactField]:
        """What this processor must be told about the payer before it will start.

        Asked rather than assumed, so the billing screen renders one provider's
        fields and not the union of every provider's. A switch then deletes a
        field instead of leaving a dead one, and a field nobody collected is a
        control that was never drawn rather than a 5xx from the provider.
        """
        ...

    async def begin_checkout(
        self, *, org_id: uuid.UUID, plan_slug: str, contact: BillingContact
    ) -> Checkout:
        """Start a subscription, and hand back where to send the customer.

        `plan_slug` is ours — `pro`, `scale` — matching `plan_prices`. Mapping it
        to whatever the processor calls a price is the implementation's job and
        the only place that mapping may live.

        The contact carries what `required_contact_fields` asked for. An
        implementation may refuse one that does not, but the screen should have
        made that impossible.
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
