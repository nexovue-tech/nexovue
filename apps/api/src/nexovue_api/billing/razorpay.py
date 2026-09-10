"""Razorpay, behind `PaymentProcessor`.

**Nothing above this module knows this file exists.** It is selected by
`PAYMENTS_DOMESTIC_PROVIDER` and reached only through the port, which is what
lets the processor change without the billing service, the routes or the screen
changing with it — ADR 0102.

Three things differ from the other adapter and each was measured rather than
assumed, on 2026-09-10 against the sandbox:

- **The hosted page comes free and is not used.** Creating a subscription
  answers with a `short_url`, and it was the first handoff here. But the create
  call takes no callback parameter, so a customer who pays on that page has
  nowhere to go and stays on it. The checkout script does take one, so the
  handoff is embedded — still no undocumented form post, and still no card
  through this application.
- **The page collects the payer's details itself**, so this processor declares no
  required contact fields — ADR 0109's whole point, and the billing screen draws
  no contact form when the set is empty.
- **A webhook is signed with a separate secret**, hex-encoded, over the raw body
  alone. The other adapter signs `timestamp + body` in base64 with the API key.

**The bill is the commitment.** A plan here is priced per *service*, and the
subscription carries a `quantity` — so the processor's own page renders
"399.00 x 5 units" and charges that every month. ADR 0111.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final

import httpx

from nexovue_api.billing import PaymentsNotConfiguredError, UnhandledBillingEventError
from nexovue_api.billing.processor import (
    BillingContact,
    BillingError,
    BillingEvent,
    BillingEventKind,
    CancellationOutcome,
    Checkout,
    Commitment,
    ContactField,
    EmbeddedCheckout,
    SignatureInvalidError,
)
from nexovue_api.settings import DOMESTIC_PAYMENT_VARIABLES, DomesticPaymentSettings

PLANS_PATH: Final = "/plans"
SUBSCRIPTIONS_PATH: Final = "/subscriptions"

# The processor's own checkout script, served by the processor so it can change
# what a card form does without a release here. Not on the API host: this is a
# browser asset and has nothing to do with `base_url`, which is where the server
# authenticates.
CHECKOUT_SCRIPT_URL: Final = "https://checkout.razorpay.com/v1/checkout.js"

# The constructor that script defines on `window`.
CHECKOUT_GLOBAL: Final = "Razorpay"
SUBSCRIPTION_CANCEL_PATH: Final = "/subscriptions/{subscription_ref}/cancel"

# https://razorpay.com/docs/webhooks/validate-test/
SIGNATURE_HEADER: Final = "x-razorpay-signature"
# Unique per delivery, so a redelivery is distinguishable from a second event —
# the property `X-GitHub-Delivery` gives door 4a and `x-idempotency-header` gives
# the other processor.
DELIVERY_HEADER: Final = "x-razorpay-event-id"

# A management call is a small request to a payment processor. Long enough for a
# slow network, short enough that a stalled gateway does not hold a request open.
TIMEOUT_SECONDS: Final = 15.0

# How many monthly charges one mandate authorises before the customer re-signs.
# A year: long enough not to interrupt anybody, short enough that a forgotten
# subscription cannot debit an account indefinitely.
BILLING_CYCLES: Final = 12
MONTHLY_PERIOD: Final = "monthly"
MONTHLY_INTERVAL: Final = 1

# Razorpay notifies the customer about the mandate itself, which the bank
# requires anyway. Ours are the product's emails, not the processor's.
PROCESSOR_NOTIFIES_CUSTOMER: Final = 1

# The note that makes a plan findable again.
#
# **Our identifier in their storage, which ADR 0110 refused for tenancy — and
# this is the case that rule does not cover.** A tenant read out of a webhook is
# a claim about whose money it is; a plan note is a lookup key for a row nobody
# is authorised by. Nothing branches on it, and a wrong value creates a duplicate
# plan rather than charging the wrong customer.
PLAN_NOTE: Final = "nexovue_plan"

# One page is far more than the handful of plans this can ever create: one per
# tier per currency per rate, and a rate change is an appended `plan_prices` row.
PLAN_PAGE_SIZE: Final = 100

# Razorpay's own event names, and what each means here.
# https://razorpay.com/docs/webhooks/payloads/subscriptions/
EVENT_KINDS: Final = {
    "subscription.activated": BillingEventKind.SUBSCRIPTION_ACTIVATED,
    "subscription.charged": BillingEventKind.PAYMENT_SUCCEEDED,
    "subscription.cancelled": BillingEventKind.SUBSCRIPTION_CANCELLED,
    # Every invoice raised and the mandate spent. The arrangement is over, which
    # is what this product does about it either way.
    "subscription.completed": BillingEventKind.SUBSCRIPTION_CANCELLED,
    # A charge failed and is being retried; then retries were exhausted. Both are
    # a payment that did not happen, and neither ends the arrangement.
    "subscription.pending": BillingEventKind.PAYMENT_FAILED,
    "subscription.halted": BillingEventKind.PAYMENT_FAILED,
}

# Accepted and acted on by nobody: authentication is the step before activation
# and is followed by it, and pause/resume/update are states this product does not
# yet distinguish. Listed rather than left to the `else` so that a genuinely new
# event name is still visible as one.
IGNORED_EVENTS: Final = frozenset(
    {
        "subscription.authenticated",
        "subscription.paused",
        "subscription.resumed",
        "subscription.updated",
    }
)

_REFUSED_DETAIL: Final = "the delivery was not signed by the configured payment account"


class RazorpayPaymentProcessor:
    """Takes money through Razorpay. Satisfies `PaymentProcessor` structurally,
    not by inheritance — which is what lets a test double exist with no import."""

    def __init__(
        self, settings: DomesticPaymentSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        if not settings.secret_key or not settings.app_id or not settings.base_url:
            # A processor built from a half-filled configuration would fail at
            # the first signature check, and a mismatching signature is the one
            # failure nobody debugs by reading the message.
            raise PaymentsNotConfiguredError(DOMESTIC_PAYMENT_VARIABLES)
        self._settings = settings
        self._client = client

    def required_contact_fields(self) -> frozenset[ContactField]:
        """None. The checkout asks the payer for what it needs.

        Still none now that the handoff prefills, and for a better reason than
        before: the one field this processor wants that we could ask for is an
        email, and the account already has one. A form that asked would be a
        form asking somebody to retype what they signed up with. ADR 0109 exists
        so a screen can find that out by asking rather than by hard-coding one
        processor's answer.
        """
        return frozenset()

    async def begin_checkout(
        self,
        *,
        org_id: uuid.UUID,
        plan_slug: str,
        contact: BillingContact,
        commitment: Commitment,
        return_url: str,
    ) -> Checkout:
        """Create the subscription and hand back what opens this processor's
        checkout on our own page.

        Two calls: find or create the plan for this tier at this rate, then a
        subscription against it with the customer's committed count as
        `quantity`.

        **Not the `short_url`.** The response carries a hosted page and it was
        used first, but nothing returns the customer from it: the create call
        takes no callback parameter, so the customer finishes on the processor's
        screen and stays there. The script does take one, which is why the
        handoff is embedded and `return_url` is passed to it.
        """
        if commitment.services < 1:
            raise BillingError(
                "a commitment of no services authorises no charge and grants no "
                "allowance, which is not a plan anybody meant to buy"
            )
        if commitment.rate.minor_units <= 0:
            raise BillingError(
                "the per-service rate is not a chargeable amount; a mandate for "
                "nothing would authorise a customer to be billed nothing"
            )

        plan_ref = await self._plan_for(plan_slug=plan_slug, commitment=commitment)
        created = await self._call(
            "POST",
            SUBSCRIPTIONS_PATH,
            {
                "plan_id": plan_ref,
                "total_count": BILLING_CYCLES,
                "quantity": commitment.services,
                "customer_notify": PROCESSOR_NOTIFIES_CUSTOMER,
                # Ours, and read by nobody: the tenant is resolved from the row
                # this checkout writes, never from what the processor echoes
                # back. ADR 0110.
                "notes": {"org_id": str(org_id), "plan_slug": plan_slug},
            },
        )
        subscription_ref = _text(created.get("id"))
        if not subscription_ref:
            raise BillingError(
                "the processor created a subscription without an id, so there is "
                "nothing for the customer to authorise and nothing for a webhook "
                "to name"
            )
        return Checkout(
            handoff=EmbeddedCheckout(
                script_url=CHECKOUT_SCRIPT_URL,
                global_name=CHECKOUT_GLOBAL,
                options={
                    # The key *id*. It is published in the page, which is what
                    # this half of the pair is for; `secret_key` never appears
                    # here and the test named for it says so.
                    "key": self._settings.app_id,
                    "subscription_id": subscription_ref,
                    # `redirect` is what makes the script POST to `callback_url`
                    # instead of calling a handler we would have to write. The
                    # route it posts to answers 303 to the billing screen.
                    "callback_url": return_url,
                    "redirect": True,
                    "prefill": _prefill(contact),
                },
            ),
            subscription_ref=subscription_ref,
        )

    async def _plan_for(self, *, plan_slug: str, commitment: Commitment) -> str:
        """This tier's plan at this rate, created once and found thereafter.

        Keyed on the rate as well as the tier, because a repricing appends a
        `plan_prices` row rather than editing one — so an old mandate keeps
        charging the rate it was signed at, which is the behaviour ADR 0083's
        effective dates exist to give.
        """
        note = _plan_note(plan_slug=plan_slug, commitment=commitment)
        existing = await self._call("GET", f"{PLANS_PATH}?count={PLAN_PAGE_SIZE}")
        for plan in existing.get("items") or []:
            if isinstance(plan, dict) and (plan.get("notes") or {}).get(PLAN_NOTE) == note:
                return _text(plan.get("id"))

        created = await self._call(
            "POST",
            PLANS_PATH,
            {
                "period": MONTHLY_PERIOD,
                "interval": MONTHLY_INTERVAL,
                "item": {
                    "name": f"Nexovue {plan_slug.title()}",
                    # Per service. The subscription's `quantity` multiplies it,
                    # and the processor's page shows both.
                    "amount": commitment.rate.minor_units,
                    "currency": commitment.rate.currency.upper(),
                    "description": "Per service monitored, per month",
                },
                "notes": {PLAN_NOTE: note},
            },
        )
        plan_ref = _text(created.get("id"))
        if not plan_ref:
            raise BillingError("the processor created a plan without an id")
        return plan_ref

    async def cancel(self, *, subscription_ref: str) -> CancellationOutcome:
        """End a subscription, and treat one that is already over as success.

        `cancel_at_cycle_end` is 0: the customer asked to stop, and the plan
        returns to the free tier on the processor's event either way. Leaving it
        running to a boundary would keep charging somebody who cancelled.
        """
        try:
            await self._call(
                "POST",
                SUBSCRIPTION_CANCEL_PATH.format(subscription_ref=subscription_ref),
                {"cancel_at_cycle_end": 0},
            )
        except BillingError:
            # Idempotent by *asking*, not by pattern-matching an error message:
            # a subscription already in a terminal state is what the caller
            # wanted, and reading its status is deterministic where guessing
            # which of a vendor's messages means "already cancelled" is not.
            status = await self._status_of(subscription_ref)
            if status not in TERMINAL_STATUSES:
                raise
            # Reported over rather than cancelled just now, so no event follows.
            return CancellationOutcome.ALREADY_ENDED
        return CancellationOutcome.REQUESTED

    async def _status_of(self, subscription_ref: str) -> str:
        """This subscription's status, or "" when it cannot be read."""
        try:
            body = await self._call("GET", f"{SUBSCRIPTIONS_PATH}/{subscription_ref}")
        except BillingError:
            return ""
        status = body.get("status")
        return status if isinstance(status, str) else ""

    def event_from_webhook(self, *, body: bytes, headers: dict[str, str]) -> BillingEvent:
        """Verify the signature and translate the payload into this product's words."""
        received = _lowercased(headers)
        self._verify(body=body, headers=received)

        try:
            document = json.loads(body)
        except ValueError as unreadable:
            raise BillingError("the delivery is signed but is not JSON") from unreadable
        if not isinstance(document, dict):
            raise BillingError("the delivery is signed but is not an object")

        name = document.get("event")
        if name in IGNORED_EVENTS:
            raise UnhandledBillingEventError(str(name))
        kind = EVENT_KINDS.get(name if isinstance(name, str) else "")
        if kind is None:
            raise UnhandledBillingEventError(str(name))

        subscription_ref = _text(
            _object_at(_object_at(_object_at(document, "payload"), "subscription"), "entity").get(
                "id"
            )
        )
        if not subscription_ref:
            raise BillingError(
                "the delivery names no subscription, so there is nothing to resolve it against"
            )
        return BillingEvent(
            kind=kind,
            subscription_ref=subscription_ref,
            delivery_id=received.get(DELIVERY_HEADER, ""),
        )

    def _verify(self, *, body: bytes, headers: Mapping[str, str]) -> None:
        """Refuse anything this account's webhook secret did not sign.

        The raw body alone, hex-encoded — not `timestamp + body` in base64, which
        is the other processor's scheme. Signed with the dashboard's webhook
        secret rather than the API key.
        """
        presented = headers.get(SIGNATURE_HEADER)
        if not presented:
            raise SignatureInvalidError(_REFUSED_DETAIL)

        digest = hmac.new(self._settings.webhook_secret.encode("utf-8"), body, hashlib.sha256)
        expected = digest.hexdigest().encode("ascii")

        # compare_digest, never ==: an early-exiting comparison leaks the
        # signature one byte at a time to anyone willing to time the responses.
        if not hmac.compare_digest(expected, presented.encode("utf-8")):
            raise SignatureInvalidError(_REFUSED_DETAIL)

    async def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One call, decoded. Raises on anything that is not a usable answer."""
        response = await self._send(method, path, payload)
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise BillingError(f"the payment processor refused the call: {_refusal(response)}")
        try:
            body = response.json()
        except ValueError as unreadable:
            raise BillingError(
                "the payment processor answered with something that is not JSON"
            ) from unreadable
        if not isinstance(body, dict):
            raise BillingError("the payment processor answered with something unusable")
        return body

    async def _send(self, method: str, path: str, payload: dict[str, Any] | None) -> httpx.Response:
        url = f"{self._settings.base_url}{path}"
        # HTTP Basic, key id as the user and the API secret as the password.
        credentials = base64.b64encode(
            f"{self._settings.app_id}:{self._settings.secret_key}".encode()
        ).decode("ascii")
        headers = {"Authorization": f"Basic {credentials}"}
        try:
            if self._client is not None:
                return await self._client.request(
                    method, url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
                )
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                return await client.request(method, url, json=payload, headers=headers)
        except httpx.HTTPError as failure:
            raise BillingError(f"could not reach the payment processor: {failure}") from failure


# A subscription in one of these can never charge again, so a cancel that finds
# one has already achieved what it was asked for.
TERMINAL_STATUSES: Final = frozenset({"cancelled", "completed", "expired"})


def _prefill(contact: BillingContact) -> dict[str, str]:
    """What the checkout starts with in its own fields.

    **This is a correction, not a convenience.** The checkout is served from the
    processor's origin, which is the same origin for every merchant embedding
    it, so it arrives already holding whatever the payer last typed on *another*
    merchant's site. Left to itself it writes that address onto the
    subscription, and the processor then sends every mandate notice — the
    pre-debit warning, the failed charge, the expiring card — to a mailbox that
    has nothing to do with this account. Measured on 2026-09-10: an address from
    an unrelated site reached `customer_email` on two live subscriptions.

    Only what is known is sent. An empty value would overwrite the payer's own
    answer with nothing, which is worse than the stale one it replaces, and the
    customer can still type over any of this.
    """
    known = {"email": contact.email, "name": contact.name, "contact": contact.phone}
    return {field: value for field, value in known.items() if value}


def _plan_note(*, plan_slug: str, commitment: Commitment) -> str:
    """What makes one plan at the processor findable again, and distinct from a
    repricing of the same tier."""
    return f"{plan_slug}:{commitment.rate.currency.upper()}:{commitment.rate.minor_units}"


def _object_at(document: Mapping[str, Any], key: str) -> dict[str, Any]:
    """A nested object, or an empty one. A payload missing a level is a payload
    naming no subscription, which the caller refuses by name."""
    value = document.get(key)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _lowercased(headers: Mapping[str, str]) -> dict[str, str]:
    """Header names, folded. HTTP field names are case-insensitive (RFC 9110),
    and a `dict` has already lost whatever case-insensitivity the framework had."""
    return {name.lower(): value for name, value in headers.items()}


def _refusal(response: httpx.Response) -> str:
    """The processor's own reason, when it gave one.

    Passing it through matters more than it looks: "authentication failed" and
    "subscription not found" are the two that actually happen, and a bare status
    code tells whoever is reading the log neither. The body of a *refusal*
    carries no credential — the headers do, and they are not touched here.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("description") if isinstance(error, dict) else None
    return str(message) if message else f"HTTP {response.status_code}"
