"""Cashfree, behind `PaymentProcessor`.

**The only module in the API that knows a payment processor exists.** ADR 0102
puts the port next door and `tests/test_provider_ports.py` refuses a vendor's
name there; every piece of Cashfree-shaped knowledge belongs here and nowhere
above.

**Why the HTTP calls and not Cashfree's SDK.** `resend.py` records the house
reasoning for the same trade, and it applies harder here: `cashfree-pg` is a
generated client covering orders, refunds, disputes, token vault, splits and
payouts, of which this product uses two endpoints. A vendor package for two
calls buys convenience and costs a dependency whose releases we inherit — on the
one path where a broken release means money.

**The webhook is the security boundary and the reason this file is shaped the
way it is.** The endpoint is public: anything on the internet can POST to it, and
the only thing separating a real billing event from a forged one is an HMAC over
the exact bytes that arrived. So `event_from_webhook` works on raw bytes,
compares in constant time, and refuses before it parses anything — the same
order `deploys/signatures.py` follows, for the same reason.

**Everything here was written against the documentation and driven through a
mock transport. No request has ever been made to Cashfree from this code**,
sandbox or otherwise. The endpoint paths, header names and payload shapes below
carry the URL they came from.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import httpx

from nexovue_api.billing import PaymentsNotConfiguredError, UnhandledBillingEventError
from nexovue_api.billing.processor import (
    BillingContact,
    BillingError,
    BillingEvent,
    BillingEventKind,
    Checkout,
    Commitment,
    ContactField,
    SignatureInvalidError,
)
from nexovue_api.settings import DOMESTIC_PAYMENT_VARIABLES, DomesticPaymentSettings

# Pinned, never "latest". Cashfree versions its API by date in a header, and an
# unpinned integration changes shape underneath us on the vendor's schedule.
# https://www.cashfree.com/docs/api-reference/payments/latest/subscription/mandate/create
API_VERSION: Final = "2026-01-01"

API_VERSION_HEADER: Final = "x-api-version"
CLIENT_ID_HEADER: Final = "x-client-id"
CLIENT_SECRET_HEADER: Final = "x-client-secret"

# https://www.cashfree.com/docs/payments/online/webhooks/signature-verification
#   signedPayload   := timestamp + rawPayload
#   expectedSignature := Base64Encode(HMACSHA256(signedPayload, merchantSecretKey))
SIGNATURE_HEADER: Final = "x-webhook-signature"
TIMESTAMP_HEADER: Final = "x-webhook-timestamp"

# Unique per unique payload, from webhook version 2025-01-01 onwards. It is what
# makes a redelivery distinguishable from a second real event, the same property
# `X-GitHub-Delivery` gives door 4a — and Cashfree delivers **at least once**, so
# duplicates are expected rather than exceptional.
# https://www.cashfree.com/docs/payments/online/webhooks/overview
DELIVERY_HEADER: Final = "x-idempotency-header"

# Ends a mandate. The path names the *merchant's* subscription id, which is the
# one this product generates and the one every webhook echoes back — never
# `cf_subscription_id`.
# https://github.com/cashfree/cashfree-pg/blob/main/docs/SubscriptionAPI.md
SUBSCRIPTION_PATH: Final = "/subscriptions/{subscription_ref}"
SUBSCRIPTION_MANAGE_PATH: Final = "/subscriptions/{subscription_ref}/manage"
SUBSCRIPTIONS_PATH: Final = "/subscriptions"
CANCEL_ACTION: Final = "CANCEL"

# Where a customer is sent to authorise the mandate.
#
# **Undocumented, and read out of the processor's own browser SDK rather than
# guessed.** Their documented path is a JavaScript call: hand `subsSessionId` to
# `cashfree.subscriptionsCheckout()` and it builds a hidden form and POSTs it
# here. Reading the SDK showed there is no URL to redirect to because the session
# id travels in a *body*, and that the response is a 302 whose `Location` is an
# ordinary page.
#
# So this module makes that POST itself and hands the `Location` back. Measured
# on 2026-09-09, not assumed: a GET here answers 404, the POST answers 302, and
# the URL it returns loads for a different client with a different user agent —
# twice — and renders the real mandate page in a browser.
#
# **The alternative was running their SDK on our own origin**, which trades this
# risk for a permanent third-party script on a signed-in page. This endpoint is
# the one their own SDK depends on, so it will not move casually, and when it
# does move `begin_checkout` fails loudly rather than behaving differently.
CHECKOUT_HANDOFF_PATH: Final = "/view/sessions/checkout/subs"
SESSION_FIELD: Final = "subs_session_id"
LOCATION_HEADER: Final = "location"

# What the handoff answers with. Every 3xx that carries a `Location` is a place
# to send the customer; the specific code is the processor's business.
_REDIRECT_CODES: Final = frozenset({301, 302, 303, 307, 308})

# Our own name for the arrangement, which the create call takes as a *request*
# field and echoes back. It is what every webhook carries and what ADR 0110
# resolves a tenant from, so it is minted here and never read out of a response.
SUBSCRIPTION_REF_PREFIX: Final = "nexovue-"

# ADR 0056 bills per service monitored, so what a month comes to moves with the
# customer's own count. `PERIODIC` is a fixed debit at a fixed interval and would
# charge the same amount whatever they ran; `ON_DEMAND` authorises a ceiling and
# leaves the amount to each charge.
# https://www.cashfree.com/docs/api-reference/payments/latest/subscription/create-subscription
PLAN_TYPE_ON_DEMAND: Final = "ON_DEMAND"

# How many monthly debits one mandate authorises before the customer re-signs.
# A mandate cannot be open-ended, so this is a real choice: a year is long enough
# not to interrupt anybody and short enough that a forgotten subscription cannot
# debit an account indefinitely.
PLAN_MAX_CYCLES: Final = 12
MANDATE_YEARS: Final = 1

# What the bank verifies the mandate with. The smallest chargeable amount, and
# refunded by the processor — it proves the account exists without taking money
# for a service the customer has not used yet.
AUTHORISATION_AMOUNT: Final = Decimal("1.00")

# A management call is a small POST to a payment processor. Long enough for a
# slow network, short enough that a stalled gateway does not hold a request open.
TIMEOUT_SECONDS: Final = 15.0

# The only scheme this processor will register a return address under **in
# production**. Checked here rather than trusted, because the rule bites at
# go-live — long after the line that built the URL was written.
# https://www.cashfree.com/docs/payments/online/go-live/whitelist
SECURE_URL_SCHEME: Final = "https://"

# The environment the rule above applies in. Measured on 2026-09-09 rather than
# assumed: their sandbox accepts `http://localhost:15173` and answers 200, so
# refusing it here was this module being stricter than the processor and making
# the checkout untestable on a developer's machine for a rule that does not
# apply there.
#
# **Nothing is weakened by scoping it.** The return URL carries no token and
# grants nothing — it is the billing screen, and the plan moves on the webhook
# (ADR 0110), never on the customer landing back. What it protects against is a
# production deployment registering a return address the processor will reject,
# and that is exactly the environment it still fires in.
SECURE_RETURN_URL_ENVIRONMENT: Final = "production"


# Cashfree's own event names, and the statuses that qualify one of them.
# https://www.cashfree.com/docs/api-reference/payments/latest/subscription/webhooks
STATUS_CHANGED_EVENT: Final = "SUBSCRIPTION_STATUS_CHANGED"
PAYMENT_SUCCESS_EVENT: Final = "SUBSCRIPTION_PAYMENT_SUCCESS"
PAYMENT_FAILED_EVENT: Final = "SUBSCRIPTION_PAYMENT_FAILED"

# The status a mandate reaches when the customer has authorised it, and the two
# it reaches when the arrangement ends by somebody's choice. Everything else in
# Cashfree's list — ON_HOLD, BANK_APPROVAL_PENDING, CARD_EXPIRED — is a state
# this product does not yet act on, and inventing an enum member for one would
# make the translation layer a copy of the vendor's changelog.
ACTIVE_STATUS: Final = "ACTIVE"
CANCELLED_STATUSES: Final = frozenset({"CANCELLED", "CUSTOMER_CANCELLED"})

# A subscription in one of these can never charge again, so a cancel that finds
# one has already achieved what it was asked for.
TERMINAL_STATUSES: Final = CANCELLED_STATUSES | frozenset(
    {"COMPLETED", "EXPIRED", "LINK_EXPIRED", "CARD_EXPIRED"}
)


class CashfreePaymentProcessor:
    """Takes money through Cashfree. Satisfies `PaymentProcessor` structurally,
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
        # A client may be passed in so a test can drive a transport without a
        # network, and so a caller that already owns a pool can share it.
        self._client = client

    def required_contact_fields(self) -> frozenset[ContactField]:
        """Name, email and phone — all three, and the phone is the one that costs.

        Cashfree requires `customer_phone` on the create call and its hosted page
        does not collect customer details, so the number has to reach us before
        the redirect. Razorpay's and Stripe's pages ask the customer themselves;
        this is the outlier, which is why the requirement is declared here rather
        than assumed by the port. ADR 0109.
        """
        return frozenset({ContactField.NAME, ContactField.EMAIL, ContactField.PHONE})

    async def begin_checkout(
        self,
        *,
        org_id: uuid.UUID,
        plan_slug: str,
        contact: BillingContact,
        commitment: Commitment,
        return_url: str,
    ) -> Checkout:
        """Create the mandate and hand back where to authorise it.

        Two calls, and the second is the one that is not in their documentation.
        The create call answers with a session id for their browser SDK rather
        than a link, so `CHECKOUT_HANDOFF_PATH` turns that session into the URL
        the port promises — see that constant for what was measured and why this
        is preferred to running their SDK on our own origin.

        **The reference is minted here, not read back.** `subscription_id` is a
        request field, so this module names the arrangement and the processor
        merely stores it, which is the opposite of the tags ADR 0110 removed.

        The input checks run first so a caller learns which of its own inputs is
        wrong before a network call can fail for an unrelated reason.
        """
        missing = contact.missing(self.required_contact_fields())
        if missing:
            raise BillingError(
                "the billing contact is missing "
                + ", ".join(sorted(field.value for field in missing))
                + ", which this processor declared it needs"
            )
        authorised_maximum = commitment.total()
        if authorised_maximum.minor_units <= 0:
            raise BillingError(
                "the authorised maximum is not a chargeable amount; a mandate "
                "for nothing would authorise a customer to be billed nothing"
            )
        # Raises on a currency whose smallest unit this product cannot name.
        # Called for that check: this processor takes major units, so an amount
        # it cannot express is a refusal here rather than a wrong charge there.
        authorised_maximum.major_units()
        if (
            self._settings.environment == SECURE_RETURN_URL_ENVIRONMENT
            and not return_url.startswith(SECURE_URL_SCHEME)
        ):
            # Their go-live rules refuse anything else, and a link rejected at
            # registration is found months after it was written. Sandbox accepts
            # a loopback address, so a developer is not held to a production rule.
            raise BillingError(
                f"the return url must begin with {SECURE_URL_SCHEME} in "
                f"{SECURE_RETURN_URL_ENVIRONMENT}, which is the only scheme this "
                f"processor will register"
            )

        subscription_ref = f"{SUBSCRIPTION_REF_PREFIX}{uuid.uuid4()}"
        created = await self._create_subscription(
            subscription_ref=subscription_ref,
            plan_slug=plan_slug,
            contact=contact,
            commitment=commitment,
            return_url=return_url,
        )
        session_id = _text(created.get("subscription_session_id"))
        if not session_id:
            raise BillingError(
                "the processor created the subscription without a checkout "
                "session, so there is nowhere to send the customer"
            )
        return Checkout(
            url=await self._handoff_url(session_id),
            subscription_ref=subscription_ref,
        )

    async def _create_subscription(
        self,
        *,
        subscription_ref: str,
        plan_slug: str,
        contact: BillingContact,
        commitment: Commitment,
        return_url: str,
    ) -> dict[str, Any]:
        """The mandate, as this processor wants it described.

        `plan_details` is sent inline rather than as a `plan_id`: a stored plan
        would be a second place the price lives, and ADR 0083 puts the rate in
        `plan_prices` with an effective date. The amount here is the *ceiling*
        the customer chose, and the plan type is what makes it a ceiling.
        """
        # An identifier for the payer that is stable for this arrangement and
        # says nothing about them. Their name, email and phone go in the call
        # because the bank's pre-debit notice needs them; ADR 0109 is why we keep
        # no copy.
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "subscription_id": subscription_ref,
            "customer_details": {
                "customer_name": contact.name,
                "customer_email": contact.email,
                "customer_phone": contact.phone,
            },
            "plan_details": {
                "plan_name": plan_slug,
                "plan_type": PLAN_TYPE_ON_DEMAND,
                "plan_currency": commitment.rate.currency.upper(),
                "plan_max_amount": float(commitment.total().major_units()),
                "plan_max_cycles": PLAN_MAX_CYCLES,
            },
            "authorisation_details": {"authorisation_amount": float(AUTHORISATION_AMOUNT)},
            "subscription_meta": {"return_url": return_url},
            "subscription_expiry_time": now.replace(year=now.year + MANDATE_YEARS).isoformat(),
        }
        response = await self._send("POST", SUBSCRIPTIONS_PATH, payload=payload)
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise BillingError(f"the processor refused to create the mandate: {_refusal(response)}")
        try:
            body = response.json()
        except ValueError as unreadable:
            raise BillingError(
                "the processor answered the create call with something that is not JSON"
            ) from unreadable
        if not isinstance(body, dict):
            raise BillingError("the processor answered the create call with something unusable")
        return body

    async def _handoff_url(self, session_id: str) -> str:
        """Turn a checkout session into the URL the port promises.

        A form POST, because that is what their SDK does and the endpoint answers
        404 to a GET. Redirects are deliberately **not** followed: the `Location`
        *is* the answer, and following it would fetch a page nobody is looking at.

        The URL that comes back is a bearer credential — whoever holds it can
        authorise the mandate — so it is returned to the caller and never logged,
        stored or put in an error message.
        """
        response = await self._send_form(CHECKOUT_HANDOFF_PATH, {SESSION_FIELD: session_id})
        location = _text(response.headers.get(LOCATION_HEADER))
        if response.status_code not in _REDIRECT_CODES or not location:
            raise BillingError(
                "the processor did not answer the checkout handoff with a "
                "redirect, so there is no page to send the customer to"
            )
        return location

    async def cancel(self, *, subscription_ref: str) -> None:
        """End a subscription, and treat one that is already over as success.

        Idempotent by *asking*, not by pattern-matching an error message. When
        the cancel is refused, the subscription is fetched: if it is already in a
        terminal state the caller got what it wanted, and anything else is a real
        failure. Reading a status is deterministic; guessing which of a vendor's
        error strings means "already cancelled" is not, and it is the kind of
        guess that silently stops working after a wording change.
        """
        refusal = await self._request(
            "POST",
            SUBSCRIPTION_MANAGE_PATH.format(subscription_ref=subscription_ref),
            payload={"subscription_id": subscription_ref, "action": CANCEL_ACTION},
        )
        if refusal is None:
            return

        if await self._status_of(subscription_ref) in TERMINAL_STATUSES:
            return
        raise BillingError(refusal)

    def event_from_webhook(self, *, body: bytes, headers: dict[str, str]) -> BillingEvent:
        """Verify a delivery's signature and translate it into our vocabulary.

        The order is the security property: the signature is checked over the
        exact bytes that arrived, before this method looks at the body in any
        other way. Nothing below the verification runs on an unsigned request.
        """
        present = _lowercased(headers)
        self._verify(body=body, headers=present)

        delivery_id = present.get(DELIVERY_HEADER, "")
        if not delivery_id:
            # Without it a captured delivery is indistinguishable from a genuine
            # retry, and Cashfree retries by design. Refusing is the deliberate
            # choice: this is the money path, and replay protection is not
            # optional on it. The header exists from webhook version 2025-01-01,
            # which is below the version this integration pins.
            raise SignatureInvalidError(
                f"the delivery carried no {DELIVERY_HEADER}, so a replay of it "
                f"could not be told apart from a redelivery"
            )

        return _translate(_parsed(body), delivery_id=delivery_id)

    def _verify(self, *, body: bytes, headers: Mapping[str, str]) -> None:
        """Refuse anything this account's secret did not sign.

        Cashfree signs `timestamp + raw body`, base64 of an HMAC-SHA256 keyed on
        the merchant secret. The timestamp is part of what is signed, so it
        cannot be altered without breaking the signature, and it is used here as
        bytes rather than parsed — its units are the vendor's business, not this
        function's.
        """
        timestamp = headers.get(TIMESTAMP_HEADER)
        presented = headers.get(SIGNATURE_HEADER)
        if not timestamp or not presented:
            raise SignatureInvalidError(_REFUSED_DETAIL)

        signed = timestamp.encode("utf-8") + body
        digest = hmac.new(self._settings.secret_key.encode("utf-8"), signed, hashlib.sha256)
        expected = base64.b64encode(digest.digest())

        # compare_digest, never ==: an early-exiting comparison leaks the
        # signature one byte at a time to anyone willing to time the responses.
        # Compared as bytes because a header is arbitrary text and the str form
        # raises on anything outside ASCII.
        if not hmac.compare_digest(expected, presented.encode("utf-8")):
            raise SignatureInvalidError(_REFUSED_DETAIL)

    async def _status_of(self, subscription_ref: str) -> str:
        """This subscription's status at the processor, or "" if it cannot be read."""
        body = await self._read(SUBSCRIPTION_PATH.format(subscription_ref=subscription_ref))
        status = body.get("subscription_status") if body else None
        return str(status) if isinstance(status, str) else ""

    async def _request(self, method: str, path: str, *, payload: dict[str, Any]) -> str | None:
        """Make one call. None when it succeeded, the processor's own reason when
        it refused. A transport failure raises, because "unreachable" and
        "refused" need different responses: one is retried, the other cannot be."""
        response = await self._send(method, path, payload=payload)
        if response.status_code >= httpx.codes.BAD_REQUEST:
            return _refusal(response)
        return None

    async def _read(self, path: str) -> dict[str, Any] | None:
        """One GET, decoded, or None when the processor did not answer with an
        object. A failed read is not raised here: the only caller is deciding
        whether a cancel already happened, and "could not tell" is not "cancelled"."""
        response = await self._send("GET", path, payload=None)
        if response.status_code >= httpx.codes.BAD_REQUEST:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def _send_form(self, path: str, fields: dict[str, str]) -> httpx.Response:
        """One form POST, and the only call here that is not JSON.

        Redirects are not followed, because the redirect is the result. No API
        credentials are sent: this endpoint takes a checkout session, which is
        already the bearer token for exactly one mandate, and adding the account
        secret to a request that does not need it widens where it travels.
        """
        url = f"{self._settings.base_url}{path}"
        try:
            if self._client is not None:
                return await self._client.post(
                    url, data=fields, timeout=TIMEOUT_SECONDS, follow_redirects=False
                )
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
                return await client.post(url, data=fields)
        except httpx.HTTPError as failure:
            raise BillingError(f"could not reach the payment processor: {failure}") from failure

    async def _send(
        self, method: str, path: str, *, payload: dict[str, Any] | None
    ) -> httpx.Response:
        url = f"{self._settings.base_url}{path}"
        headers = {
            API_VERSION_HEADER: API_VERSION,
            CLIENT_ID_HEADER: self._settings.app_id,
            CLIENT_SECRET_HEADER: self._settings.secret_key,
        }
        try:
            if self._client is not None:
                return await self._client.request(
                    method, url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
                )
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                return await client.request(method, url, json=payload, headers=headers)
        except httpx.HTTPError as failure:
            raise BillingError(f"could not reach the payment processor: {failure}") from failure


# One sentence for every way a delivery failed to prove itself. It distinguishes
# no cause on purpose: an unsigned request and a wrongly signed one are the same
# answer, and saying which would tell a forger how close they got.
_REFUSED_DETAIL: Final = "the delivery was not signed by the configured payment account"


def _lowercased(headers: Mapping[str, str]) -> dict[str, str]:
    """Header names, folded. HTTP field names are case-insensitive (RFC 9110),
    and a `dict` has already lost whatever case-insensitivity the framework had."""
    return {name.lower(): value for name, value in headers.items()}


def _parsed(body: bytes) -> dict[str, Any]:
    """The signed bytes as an object. Reached only after the signature held."""
    try:
        document = json.loads(body)
    except ValueError as error:
        raise BillingError("the signed delivery was not JSON") from error
    if not isinstance(document, dict):
        raise BillingError("the signed delivery was not a JSON object")
    return document


def _translate(document: Mapping[str, Any], *, delivery_id: str) -> BillingEvent:
    """One of Cashfree's events, said in this product's words.

    Everything a caller downstream sees is decided here, and nothing of the
    original leaves this function — which is the whole reason the port names a
    `BillingEvent` rather than handing a plan change some vendor's JSON.
    """
    data = _object_at(document, "data")
    details = _object_at(data, "subscription_details")

    # No `subscription_tags` are read any more. They carried our organization id
    # and plan slug, echoed back by the processor — which meant the only create
    # call that returns a redirectable URL could not be used, because it cannot
    # carry tags. The tenant is now resolved from our own record of the
    # subscription, in the service layer. ADR 0110.
    kind = _kind_of(document.get("type"), details.get("subscription_status"))

    # The merchant-side id, which is what every later call names. Read from
    # `subscription_details` first because that object is present on the status
    # and payment events alike; the payment events also carry it at the top of
    # `data`.
    subscription_ref = _text(details.get("subscription_id") or data.get("subscription_id"))
    if not subscription_ref:
        # Refused rather than passed on empty. Since ADR 0110 this reference is
        # the *only* thing tying an event to a tenant, so an empty one is not a
        # missing detail — it is an event that cannot be attributed to anybody,
        # and letting it through would hand the resolver a value that matches
        # whatever row happens to hold a blank reference.
        raise BillingError(
            "the delivery names no subscription, so there is nothing to resolve it against"
        )

    return BillingEvent(kind=kind, subscription_ref=subscription_ref, delivery_id=delivery_id)


def _kind_of(event_type: Any, subscription_status: Any) -> BillingEventKind:
    """Which of the four members this event is, or a drop.

    A status change is the one that needs the status as well as the name:
    Cashfree sends `SUBSCRIPTION_STATUS_CHANGED` for ten different statuses, and
    only two of them are things this product acts on.
    """
    name = _text(event_type)
    if name == PAYMENT_SUCCESS_EVENT:
        return BillingEventKind.PAYMENT_SUCCEEDED
    if name == PAYMENT_FAILED_EVENT:
        return BillingEventKind.PAYMENT_FAILED
    if name == STATUS_CHANGED_EVENT:
        status = _text(subscription_status)
        if status == ACTIVE_STATUS:
            return BillingEventKind.SUBSCRIPTION_ACTIVATED
        if status in CANCELLED_STATUSES:
            return BillingEventKind.SUBSCRIPTION_CANCELLED
        raise UnhandledBillingEventError(f"a subscription moving to {status or 'no status'}")
    raise UnhandledBillingEventError(f"a {name or 'nameless'} event")


def _object_at(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A nested object, or an empty one. Cashfree sends `null` rather than
    omitting an unset object, so an absent branch is normal, not malformed."""
    value = document.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _refusal(response: httpx.Response) -> str:
    """The processor's own reason, when it gave one.

    Cashfree answers a refusal with a JSON body carrying `message`. Passing that
    through matters more than it looks: "authentication failed" and "subscription
    not found" are the two that actually happen, and a bare status code tells
    whoever is reading the log neither. The body of a *refusal* carries no
    credential — the request's headers do, and they are not touched here.
    """
    try:
        detail = response.json().get("message") or response.text
    except (ValueError, AttributeError):
        detail = response.text
    return f"the payment processor refused the call ({response.status_code}): {detail}"
