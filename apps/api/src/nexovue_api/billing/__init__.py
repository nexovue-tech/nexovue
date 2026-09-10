"""Payments: the port, its implementations, and the one place that chooses
between them.

**`build_payment_processor` is the composition root, and it is the third part of
the pattern `notifications` established.** A port and an adapter alone still
make a caller name a processor at construction; this is what keeps that name in
one file. Every caller asks for `PaymentProcessor` and never learns what
answered.

Which processor is used comes from `PAYMENTS_DOMESTIC_PROVIDER` in the
environment, so switching one is configuration rather than a code change. The
vendor's name is the *value* — every other `PAYMENTS_DOMESTIC_*` variable stays
neutral and survives the switch.

ADR 0102 expects a *second* root here eventually — a merchant of record for
customers outside India, because a non-EU seller owes EU VAT from the first
euro. That is why this one says `domestic` in its name rather than owning the
word "payments" outright.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexovue_api.billing.processor import (
    BillingContact,
    BillingError,
    BillingEvent,
    BillingEventKind,
    CancellationOutcome,
    Checkout,
    Commitment,
    ContactField,
    CurrencyUnknownError,
    Money,
    PaymentProcessor,
    SignatureInvalidError,
)
from nexovue_api.settings import DomesticPaymentProvider, DomesticPaymentSettings

__all__ = [
    "BillingContact",
    "BillingError",
    "BillingEvent",
    "BillingEventKind",
    "CancellationOutcome",
    "Checkout",
    "Commitment",
    "ConfiguredProcessor",
    "ContactField",
    "CurrencyUnknownError",
    "Money",
    "PaymentProcessor",
    "PaymentsNotConfiguredError",
    "SignatureInvalidError",
    "UnhandledBillingEventError",
    "build_payment_processor",
]


@dataclass(frozen=True, slots=True)
class ConfiguredProcessor:
    """A processor, and the name its rows are filed under.

    Two fields because the port deliberately cannot say who it is: every caller
    talks to `processor` and never learns what answered, while a stored row has
    to record which processor an arrangement lives at — ADR 0102 expects two of
    them. `name` is the settings *value*, so it is the one place a vendor's name
    crosses out of this package, and it crosses as data rather than as code.
    """

    name: str
    processor: PaymentProcessor


class PaymentsNotConfiguredError(BillingError):
    """Money was going to move and no processor is configured.

    Its own type because the remedy differs from every other `BillingError`:
    those are a processor refusing or unreachable, where this is an operator who
    has not set four variables. The message names them, because a caller staring
    at "could not start checkout" has no way to guess which one is missing.
    """

    def __init__(self, variables: tuple[str, ...]) -> None:
        super().__init__(
            "payments are not configured; set "
            + ", ".join(variables)
            + " to take money or receive billing events"
        )


class UnhandledBillingEventError(BillingError):
    """A correctly signed webhook this product does not act on.

    `BillingEventKind` is a closed set on purpose, and the port says an
    implementation *drops* an event that maps to no member rather than inventing
    one. `event_from_webhook` returns a `BillingEvent`, so dropping needs a way
    to be said out loud — this is it.

    **Not a failure.** A processor sends far more than four kinds of event, and
    the caller answers this with a 200: anything else asks the processor to
    redeliver an event that will be dropped again on every attempt.
    """

    def __init__(self, description: str) -> None:
        super().__init__(f"this product does not act on {description}")


def build_payment_processor(settings: DomesticPaymentSettings) -> PaymentProcessor:
    """The one place a payment processor is chosen.

    A match rather than a registry dict: `DomesticPaymentProvider` is a
    `Literal`, so mypy fails the day a processor is added to it and not handled
    here. A dict keyed by string would accept the new name and raise at runtime
    instead — during a checkout, in front of somebody trying to pay.
    """
    provider: DomesticPaymentProvider = settings.provider
    match provider:
        case "razorpay":
            # Imported here rather than at module scope so adding a processor
            # does not make every caller import every vendor's client.
            from nexovue_api.billing.razorpay import RazorpayPaymentProcessor

            return RazorpayPaymentProcessor(settings)
