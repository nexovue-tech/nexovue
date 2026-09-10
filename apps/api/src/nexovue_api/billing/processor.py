"""The port every payment leaves and every billing event arrives through.

The domestic processor is Razorpay. This interface existed before that choice
and outlives it — it has already survived one switch and back — because a
domestic Indian gateway leaves us the legal seller and therefore liable for
foreign VAT registration, while a merchant of record becomes the seller and
carries that instead. Those are different companies, different compliance, and
probably both — one for India and one for everywhere else.

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
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Final, Protocol


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
    """One webhook, translated. The only shape billing logic ever sees.

    **It names the processor's subscription, not our tenant, and that is a
    correction made on 2026-09-09.** It carried `org_id` and `plan_slug`, read
    out of custom tags we asked the processor to echo back — which was us
    borrowing the provider's storage for our own identifiers, and it cost more
    than it looked: at the domestic processor, the one create call that returns a
    redirectable URL cannot carry tags at all, so `Checkout.url` and this
    translation could not both be satisfied. Which processor that is belongs in
    ADR 0110, not here.

    A webhook naturally identifies *its own* subscription. Whose it is, is our
    question, answered from `billing_subscriptions` where we recorded it when the
    checkout began — one indexed lookup, in the service layer, leaving this a
    pure translation of bytes into meaning. See ADR 0110.
    """

    kind: BillingEventKind
    # The processor's own subscription identifier, and the only identity a
    # webhook is required to carry. Opaque: nothing parses or branches on it.
    subscription_ref: str
    # Unique per delivery, so a replayed webhook is detectable — the same
    # property `X-GitHub-Delivery` gives door 4a.
    delivery_id: str


@dataclass(frozen=True, slots=True)
class HostedPage:
    """The processor owns a whole page; send the browser to it.

    Simple, and it costs the customer the end of the journey: a processor whose
    page takes no return address leaves them standing on it, having just paid,
    with nothing to press.
    """

    url: str


@dataclass(frozen=True, slots=True)
class EmbeddedCheckout:
    """The processor's own script takes over our page, and gives it back.

    **The card is no closer to us than in a hosted page.** The script renders
    the processor's own fields and posts them to the processor; this
    application sees a reference and never a number, which is ADR 0076's
    posture unchanged. What is gained is the ending: the customer stays on our
    domain, and `return_url` is where the processor puts them when it is done.
    """

    # Loaded on our page. The processor serves it, so the processor can change
    # what a card form does without a release here — which is the whole reason
    # such a form is never ours to write.
    script_url: str
    # The constructor that script defines on `window`. Sent rather than known,
    # because a screen that hard-codes it has learned which processor it is
    # talking to, and unlearning that is the switch this port exists to make
    # cheap.
    global_name: str
    # What to construct it with. **Opaque above the adapter**: these are the
    # script's own option names, the script is their only reader, and nothing
    # between here and the browser interprets one. That is what keeps the
    # processor's vocabulary out of the screen.
    #
    # It is rendered into a page, so everything in it is published. The
    # publishable half of a credential pair belongs here; **the secret half
    # never does.**
    options: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Checkout:
    """Where a customer authorises the mandate, however this processor does it.

    The two shapes are not a preference. A processor whose subscription API takes
    no return address can only be a `HostedPage`, and one whose script accepts a
    callback can be an `EmbeddedCheckout`; the screen draws whichever it is told
    and nothing above this file chooses.
    """

    handoff: HostedPage | EmbeddedCheckout
    # The name this arrangement is known by at the processor, and the identifier
    # its webhooks echo back. Required, because ADR 0110 makes it the only thread
    # between the checkout and the event: the row written here is what later
    # answers *whose* subscription an event is about.
    #
    # An implementation may mint it rather than receive it — at the domestic
    # processor the create call takes it as a request field — which is why it is
    # the adapter that supplies it and not the caller.
    subscription_ref: str
    # The processor's id for the payer, when it issues one at this point. Absent
    # at a processor with no customer object, and absent at one that only names
    # the payer later — which is not the same statement as "unknown payer".
    processor_customer_ref: str | None = None


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


@dataclass(frozen=True, slots=True)
class Commitment:
    """What the customer agreed to buy: a count of services, at a rate each.

    **The count and the rate travel separately because processors disagree about
    which one they want.** One takes a ceiling for the whole mandate and works
    the rest out itself; another takes a per-unit plan and a quantity, and prints
    "399.00 x 5 units" on its own page from exactly that. Handing both a
    multiplied total would force the second to divide it back out, and it has no
    way to know the divisor.

    ADR 0056 prices per service monitored, so `services` is the number the
    customer chose and `rate` is what one costs for a month.
    """

    services: int
    rate: Money

    def total(self) -> Money:
        """A month at this commitment. The most the mandate may ever debit."""
        return Money(minor_units=self.rate.minor_units * self.services, currency=self.rate.currency)


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


class CancellationOutcome(StrEnum):
    """What asking a processor to end a subscription actually achieved.

    The distinction is not bookkeeping. `REQUESTED` means the processor accepted
    the request and will report the ending in its own event, which is the only
    thing that moves a plan (ADR 0110). `ALREADY_ENDED` means it reported the
    subscription as over *when asked* — cancelled in the processor's own
    dashboard, ended by the bank, or cancelled here before anything was
    listening. No event follows that, because nothing is about to change.

    Collapsing the two is how an organization stays on a paid tier forever after
    a cancellation nobody's webhook witnessed.
    """

    REQUESTED = "requested"
    ALREADY_ENDED = "already_ended"


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
        commitment: Commitment,
        return_url: str,
    ) -> Checkout:
        """Start a subscription, and hand back where to send the customer.

        `plan_slug` is ours — `pro`, `scale` — matching `plan_prices`. Mapping it
        to whatever the processor calls a price is the implementation's job and
        the only place that mapping may live.

        **`commitment` is what the customer agreed to buy, and it is also what
        they are billed.** A month is `rate x services`, every month, whether or
        not they run that many.

        That is a change: the bill used to move with the services actually
        monitored, capped at the commitment. It now *is* the commitment. The
        product is sold on an invoice nobody has to predict, and a number that
        does not move is the strongest form of that — see ADR 0111. The ceiling
        ADR 0107 promised is unchanged in effect: a customer can never be charged
        past a figure they chose in advance, because that figure is the charge.

        `return_url` is where the processor sends the customer's browser when
        the payment reaches a terminal state. It carries no outcome and proves
        nothing — the webhook is what this product believes.

        **It is always one of this API's own routes, never a screen.** A
        processor may return the customer by POST — the domestic one does — and
        nothing serving a single-page app answers a POST to a client route, so
        the customer meets a 404 after their money has moved. An adapter must
        not rewrite it into an app path.

        The contact carries what `required_contact_fields` asked for. An
        implementation may refuse one that does not, but the screen should have
        made that impossible.
        """
        ...

    async def cancel(self, *, subscription_ref: str) -> CancellationOutcome:
        """End a subscription. Idempotent: cancelling a cancelled one is not an
        error, because a customer pressing twice must not see a failure.

        **The outcome says whether an event is still coming**, and the caller
        needs it: a subscription the processor reports as already over will
        never produce another event, so a caller that waits for one waits
        forever. See `CancellationOutcome`.
        """
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
