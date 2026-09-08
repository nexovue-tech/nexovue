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
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Final, Protocol


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


class CurrencyUnknownError(BillingError):
    """An amount arrived in a currency whose smallest unit this product cannot
    name, so it cannot be written on a wire that wants major units.

    Refused rather than divided by a hundred: the yen's minor unit *is* the yen,
    so a default exponent turns a ¥600 bill into ¥6.
    """

    def __init__(self, currency: str) -> None:
        super().__init__(
            f"no minor-unit exponent is recorded for {currency}, so an amount in "
            f"it cannot be stated in major units without guessing the divisor"
        )


# How many of a currency's smallest unit make one of its major unit — ISO 4217's
# exponent, as a divisor. Only the currencies this product actually prices in
# belong here: an entry is a claim about a real currency, and the default that
# would cover the rest is the guess `CurrencyUnknownError` exists to refuse.
MINOR_UNITS_PER_MAJOR: Final = {"USD": 100, "INR": 100, "EUR": 100, "GBP": 100}


@dataclass(frozen=True, slots=True)
class Money:
    """An amount, as a whole count of a currency's smallest unit.

    An integer, never a float: binary floating point cannot hold 0.10, and an
    amount that drifts by a hundredth is an amount a customer disputes.
    `plan_prices.price_per_service_minor_units` is stored the same way and for
    the same reason.

    The currency travels with the number because an integer alone is not an
    amount — 600 is six dollars or six hundred yen depending on a field that
    must not be somewhere else.
    """

    minor_units: int
    # ISO 4217 alpha-3, as `plan_prices.currency` holds it.
    currency: str

    def major_units(self) -> Decimal:
        """The same amount as a decimal of the currency's major unit.

        Some processors take money that way rather than in minor units. Decimal
        rather than float for the reason above, and the divisor is looked up
        rather than assumed.
        """
        divisor = MINOR_UNITS_PER_MAJOR.get(self.currency.upper())
        if divisor is None:
            raise CurrencyUnknownError(self.currency)
        return Decimal(self.minor_units) / Decimal(divisor)


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
        self,
        *,
        org_id: uuid.UUID,
        plan_slug: str,
        contact: BillingContact,
        authorised_maximum: Money,
        return_url: str,
    ) -> Checkout:
        """Start a subscription, and hand back where to send the customer.

        `plan_slug` is ours — `pro`, `scale` — matching `plan_prices`. Mapping it
        to whatever the processor calls a price is the implementation's job and
        the only place that mapping may live.

        **`authorised_maximum` is the most one billing period may charge**, not
        a fixed amount to charge every period. ADR 0056 prices per service
        monitored, so what a month comes to moves with the customer's own
        service count, and a fixed figure would be wrong the first time they
        deploy something. It is also ADR 0107's ceiling said in money: a
        customer can never be charged past a number they chose in advance.

        `return_url` is where the processor sends the customer's browser when
        the payment reaches a terminal state. It carries no outcome and proves
        nothing — the webhook is what this product believes — so it is only
        somewhere sensible to land.

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
