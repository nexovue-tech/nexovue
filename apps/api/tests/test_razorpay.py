"""Razorpay behind the port, driven through `httpx.MockTransport`.

`test_payments.py` does this for the other processor. The two suites are
deliberately separate rather than parametrised: what is worth asserting is
exactly what *differs* — a hex digest over the raw body against a base64 one over
`timestamp + body`, a per-unit plan with a quantity against a whole-mandate
ceiling, and a processor that asks for no contact details at all.

**No request has ever been made to Razorpay from this suite or the code it
drives.** The shapes below were observed once against their sandbox on
2026-09-10 and written down; nothing here reaches a network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any, Final

import httpx
import pytest

from nexovue_api.billing import (
    BillingContact,
    BillingError,
    BillingEventKind,
    CancellationOutcome,
    Commitment,
    EmbeddedCheckout,
    Money,
    PaymentsNotConfiguredError,
    SignatureInvalidError,
    UnhandledBillingEventError,
    build_payment_processor,
)
from nexovue_api.billing.razorpay import (
    CHECKOUT_SCRIPT_URL,
    DELIVERY_HEADER,
    PLAN_NOTE,
    SIGNATURE_HEADER,
    RazorpayPaymentProcessor,
)
from nexovue_api.settings import DomesticPaymentSettings

pytestmark = pytest.mark.anyio

API_SECRET: Final = "rzp_api_secret_never_signs_a_webhook"
WEBHOOK_SECRET: Final = "dashboard_webhook_secret"
BASE_URL: Final = "https://api.razorpay.com/v1"

SETTINGS: Final = DomesticPaymentSettings(
    provider="razorpay",
    app_id="rzp_test_APPID",
    secret_key=API_SECRET,
    environment="sandbox",
    base_url=BASE_URL,
    webhook_secret=WEBHOOK_SECRET,
)

ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-000000000001")
SUBSCRIPTION_REF: Final = "sub_Ta43Wksw6pBSiE"
PLAN_REF: Final = "plan_Ta43WObG3LmJSF"
SHORT_URL: Final = "https://rzp.io/rzp/Py9BGBA"
DELIVERY_ID: Final = "Ta43WevtId0000001"

# Five services at 399.00 each. The processor's own page renders this as
# "399.00 x 5 units" and charges 1,995.00 a month.
COMMITMENT: Final = Commitment(services=5, rate=Money(minor_units=39_900, currency="INR"))
CONTACT: Final = BillingContact(email="owner@example.test")
RETURN_URL: Final = "https://app.example.test/settings/billing"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """What Razorpay puts in the header, computed the way Razorpay computes it:
    HMAC-SHA256 over the raw body, hex, with the *webhook* secret."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _payload(
    event: str = "subscription.activated", subscription_ref: str | None = SUBSCRIPTION_REF
) -> bytes:
    entity: dict[str, Any] = {"status": "active", "quantity": 5}
    if subscription_ref is not None:
        entity["id"] = subscription_ref
    return json.dumps(
        {
            "entity": "event",
            "event": event,
            "payload": {"subscription": {"entity": entity}},
        }
    ).encode()


def _headers(body: bytes, **overrides: str) -> dict[str, str]:
    headers = {SIGNATURE_HEADER: _sign(body), DELIVERY_HEADER: DELIVERY_ID}
    headers.update(overrides)
    return headers


class RecordingApi:
    """Answers the calls `begin_checkout` makes, shaped as the sandbox answered."""

    def __init__(self, *, existing_plans: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._existing = existing_plans or []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/plans") and request.method == "GET":
            return httpx.Response(200, json={"entity": "collection", "items": self._existing})
        if path.endswith("/plans"):
            return httpx.Response(200, json={"id": PLAN_REF, "entity": "plan"})
        return httpx.Response(
            200,
            json={
                "id": SUBSCRIPTION_REF,
                "entity": "subscription",
                "status": "created",
                "quantity": json.loads(request.content)["quantity"],
                "short_url": SHORT_URL,
            },
        )

    def body_of(self, index: int) -> dict[str, Any]:
        return dict(json.loads(self.requests[index].content))


def _processor(handler: Any = None) -> RazorpayPaymentProcessor:
    client = (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) if handler is not None else None
    )
    return RazorpayPaymentProcessor(SETTINGS, client=client)


async def _checkout(handler: Any, *, commitment: Commitment = COMMITMENT) -> Any:
    return await _processor(handler).begin_checkout(
        org_id=ORG_ID,
        plan_slug="pro",
        contact=CONTACT,
        commitment=commitment,
        return_url=RETURN_URL,
    )


# ---------------------------------------------------------------- the signature


class TestSignatureVerification:
    """The only thing separating a real billing event from a forged one."""

    def test_a_delivery_signed_with_the_webhook_secret_is_accepted(self) -> None:
        body = _payload()

        event = _processor().event_from_webhook(body=body, headers=_headers(body))

        assert event.kind is BillingEventKind.SUBSCRIPTION_ACTIVATED
        assert event.subscription_ref == SUBSCRIPTION_REF
        assert event.delivery_id == DELIVERY_ID

    def test_a_delivery_signed_with_the_api_key_is_refused(self) -> None:
        """**The difference from the other processor**, and the one that would
        cost a day. Razorpay issues a webhook secret in its dashboard that is not
        the API credential; verifying with the API key refuses every real
        delivery with a message that reads like a forgery."""
        body = _payload()

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(
                body=body, headers=_headers(body, **{SIGNATURE_HEADER: _sign(body, API_SECRET)})
            )

    def test_a_base64_digest_is_refused(self) -> None:
        """The other processor signs in base64. Accepting either encoding would
        halve the search space of anybody guessing."""
        import base64

        body = _payload()
        digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(
                body=body,
                headers=_headers(body, **{SIGNATURE_HEADER: base64.b64encode(digest).decode()}),
            )

    def test_a_body_altered_after_signing_is_refused(self) -> None:
        body = _payload()
        headers = _headers(body)
        tampered = body.replace(b"active", b"halted")

        assert tampered != body
        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=tampered, headers=headers)

    def test_a_delivery_with_no_signature_is_refused(self) -> None:
        body = _payload()

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers={DELIVERY_HEADER: DELIVERY_ID})

    def test_header_names_are_matched_without_regard_to_case(self) -> None:
        """HTTP field names are case-insensitive and a dict has already lost it."""
        body = _payload()
        headers = {name.upper(): value for name, value in _headers(body).items()}

        assert _processor().event_from_webhook(body=body, headers=headers).subscription_ref


# --------------------------------------------------------------- the translation


class TestTranslation:
    @pytest.mark.parametrize(
        ("event", "kind"),
        [
            ("subscription.activated", BillingEventKind.SUBSCRIPTION_ACTIVATED),
            ("subscription.charged", BillingEventKind.PAYMENT_SUCCEEDED),
            ("subscription.cancelled", BillingEventKind.SUBSCRIPTION_CANCELLED),
            ("subscription.completed", BillingEventKind.SUBSCRIPTION_CANCELLED),
            ("subscription.pending", BillingEventKind.PAYMENT_FAILED),
            ("subscription.halted", BillingEventKind.PAYMENT_FAILED),
        ],
    )
    def test_each_event_this_product_acts_on(self, event: str, kind: BillingEventKind) -> None:
        body = _payload(event=event)

        assert _processor().event_from_webhook(body=body, headers=_headers(body)).kind is kind

    @pytest.mark.parametrize(
        "event",
        ["subscription.authenticated", "subscription.paused", "subscription.resumed", "made.up"],
    )
    def test_an_event_this_product_does_not_act_on_is_dropped(self, event: str) -> None:
        """`BillingEventKind` is a closed set. Growing it from a vendor's
        changelog is how a translation layer stops being one."""
        body = _payload(event=event)

        with pytest.raises(UnhandledBillingEventError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_signed_delivery_naming_no_subscription_is_refused(self) -> None:
        """ADR 0110: the reference is the whole of what a webhook must carry, so
        one without it cannot be attributed to anybody."""
        body = _payload(subscription_ref=None)

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_correctly_signed_body_that_is_not_json_is_refused(self) -> None:
        body = b"[]"

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))


# ------------------------------------------------------------------ the checkout


class TestBeginningACheckout:
    async def test_a_checkout_creates_the_subscription_and_hands_back_the_script(self) -> None:
        api = RecordingApi()

        checkout = await _checkout(api)

        assert isinstance(checkout.handoff, EmbeddedCheckout)
        assert checkout.handoff.script_url == CHECKOUT_SCRIPT_URL
        assert checkout.handoff.options["subscription_id"] == SUBSCRIPTION_REF
        # `redirect` is what sends the customer to the callback rather than to a
        # handler function this product would have to write.
        assert checkout.handoff.options["callback_url"] == RETURN_URL
        assert checkout.handoff.options["redirect"] is True
        assert checkout.subscription_ref == SUBSCRIPTION_REF

    async def test_the_account_holders_address_is_what_the_checkout_starts_with(self) -> None:
        """The checkout is served from the processor's own origin, shared by
        every merchant embedding it, so it arrives holding whatever the payer
        last typed somewhere else. Unsent, that address is written onto the
        subscription and every mandate notice goes to it."""
        checkout = await _checkout(RecordingApi())

        assert isinstance(checkout.handoff, EmbeddedCheckout)
        assert checkout.handoff.options["prefill"]["email"] == CONTACT.email

    async def test_nothing_unknown_is_prefilled(self) -> None:
        """An empty value overwrites the payer's own answer with nothing, which
        is worse than the stale one it was meant to replace."""
        sparse = BillingContact(email="someone@example.test")

        checkout = await _processor(RecordingApi()).begin_checkout(
            org_id=ORG_ID,
            plan_slug="pro",
            contact=sparse,
            commitment=COMMITMENT,
            return_url=RETURN_URL,
        )

        assert isinstance(checkout.handoff, EmbeddedCheckout)
        assert checkout.handoff.options["prefill"] == {"email": "someone@example.test"}

    async def test_the_hosted_page_is_not_the_handoff(self) -> None:
        """The create call answers with one and it is deliberately unused: it
        takes no callback, so a customer who pays on it has nowhere to go."""
        checkout = await _checkout(RecordingApi())

        assert SHORT_URL not in repr(checkout)

    async def test_only_the_publishable_key_reaches_the_browser(self) -> None:
        """This handoff is rendered into a page. The API secret in it would be
        published to every visitor of that page."""
        checkout = await _checkout(RecordingApi())

        assert isinstance(checkout.handoff, EmbeddedCheckout)
        assert checkout.handoff.options["key"] == SETTINGS.app_id
        assert API_SECRET not in repr(checkout)
        assert SETTINGS.webhook_secret not in repr(checkout)

    async def test_the_plan_is_priced_per_service_and_the_count_is_the_quantity(self) -> None:
        """ADR 0111. The processor multiplies, and renders both halves on its own
        page — so sending a multiplied total would show the customer one service
        at five times the price."""
        api = RecordingApi()

        await _checkout(api)

        plan = api.body_of(1)
        assert plan["item"]["amount"] == COMMITMENT.rate.minor_units
        assert plan["item"]["currency"] == "INR"
        assert plan["period"] == "monthly"

        subscription = api.body_of(2)
        assert subscription["quantity"] == COMMITMENT.services
        assert subscription["plan_id"] == PLAN_REF

    async def test_an_existing_plan_at_the_same_rate_is_reused(self) -> None:
        """One plan per tier per rate, not one per checkout. A gateway dashboard
        with a plan per customer is unreadable, and every one of them identical."""
        note = f"pro:INR:{COMMITMENT.rate.minor_units}"
        api = RecordingApi(existing_plans=[{"id": "plan_existing", "notes": {PLAN_NOTE: note}}])

        await _checkout(api)

        assert [request.method for request in api.requests] == ["GET", "POST"]
        assert api.body_of(1)["plan_id"] == "plan_existing"

    async def test_a_repricing_makes_a_new_plan_rather_than_reusing_the_old(self) -> None:
        """ADR 0083 appends price rows, so an old mandate keeps charging the rate
        it was signed at. Reusing a plan across rates would silently reprice it."""
        stale = f"pro:INR:{COMMITMENT.rate.minor_units - 100}"
        api = RecordingApi(existing_plans=[{"id": "plan_old", "notes": {PLAN_NOTE: stale}}])

        await _checkout(api)

        assert [request.method for request in api.requests] == ["GET", "POST", "POST"]
        assert api.body_of(2)["plan_id"] == PLAN_REF

    async def test_a_commitment_of_nothing_is_refused(self) -> None:
        api = RecordingApi()

        with pytest.raises(BillingError, match="no services"):
            await _checkout(api, commitment=Commitment(services=0, rate=COMMITMENT.rate))

    async def test_a_rate_of_nothing_is_refused(self) -> None:
        api = RecordingApi()

        with pytest.raises(BillingError, match="not a chargeable amount"):
            await _checkout(
                api, commitment=Commitment(services=3, rate=Money(minor_units=0, currency="INR"))
            )

    async def test_a_refusal_says_what_the_processor_said(self) -> None:
        def refuses(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": {"description": "The plan id provided does not exist"}}
            )

        with pytest.raises(BillingError, match="does not exist"):
            await _checkout(refuses)


# --------------------------------------------------------------- the wiring


class TestConfigurationAndWiring:
    def test_this_processor_asks_for_no_contact_details(self) -> None:
        """Its own page collects them, and its documentation says they are not
        pre-filled even when supplied — so asking first makes a customer type the
        same thing twice. ADR 0109 is why the screen can find this out."""
        assert _processor().required_contact_fields() == frozenset()

    def test_the_provider_name_selects_this_implementation(self) -> None:
        assert isinstance(build_payment_processor(SETTINGS), RazorpayPaymentProcessor)

    def test_a_half_configured_processor_refuses_to_be_built(self) -> None:
        """A missing key fails at the first signature check otherwise, and a
        mismatching signature is the one failure nobody debugs by reading it."""
        from dataclasses import replace

        with pytest.raises(PaymentsNotConfiguredError):
            RazorpayPaymentProcessor(replace(SETTINGS, secret_key=""))

    def test_no_failure_message_carries_a_secret(self) -> None:
        """Every one of these ends up in a log line or a problem document."""
        from dataclasses import replace

        messages: list[str] = []
        body = _payload()

        try:
            _processor().event_from_webhook(body=body, headers={})
        except SignatureInvalidError as refused:
            messages.append(str(refused))
        try:
            RazorpayPaymentProcessor(replace(SETTINGS, app_id=""))
        except PaymentsNotConfiguredError as unconfigured:
            messages.append(str(unconfigured))

        assert messages
        for message in messages:
            assert API_SECRET not in message
            assert WEBHOOK_SECRET not in message


class TestCancelling:
    """What the caller is told, because the answer decides whether it waits."""

    async def test_a_live_subscription_is_cancelled_and_an_event_is_expected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/cancel")
            return httpx.Response(200, json={"id": SUBSCRIPTION_REF, "status": "cancelled"})

        outcome = await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)

        assert outcome is CancellationOutcome.REQUESTED

    async def test_one_already_over_says_so_rather_than_promising_an_event(self) -> None:
        """The processor refuses the cancel and reports a terminal status. That
        is not a failure — it is the answer — and it is the case that used to
        leave an organization on a paid tier waiting for an event that could
        never arrive.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/cancel"):
                return httpx.Response(400, json={"error": {"description": "already cancelled"}})
            return httpx.Response(200, json={"id": SUBSCRIPTION_REF, "status": "cancelled"})

        outcome = await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)

        assert outcome is CancellationOutcome.ALREADY_ENDED

    async def test_a_refusal_on_a_live_subscription_is_still_a_failure(self) -> None:
        """Only a terminal status makes a refused cancel a success. A live one
        that would not cancel has to be reported, not silently swallowed."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/cancel"):
                return httpx.Response(500, json={"error": {"description": "upstream"}})
            return httpx.Response(200, json={"id": SUBSCRIPTION_REF, "status": "active"})

        with pytest.raises(BillingError):
            await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)
