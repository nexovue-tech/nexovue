"""The Cashfree processor, driven without a network (P1-73).

`httpx.MockTransport` answers in-process, so these assert what is actually put
on the wire — the path, the auth headers, the API version, the body — rather
than that a mock was called. **No request has ever been made to Cashfree from
this suite or from the code it drives**, sandbox or otherwise.

The signature half is the one that matters. It is the only thing separating a
real billing event from a forged one, so a valid signature is proved to be
accepted, and a wrong one, a missing one, one over a body altered after signing,
one from another account's secret and one bound to a different timestamp are all
proved to be refused *before* the body is read.

The scheme implemented is Cashfree's own, from
https://www.cashfree.com/docs/payments/online/webhooks/signature-verification:

    signedPayload     := timestamp + rawPayload
    expectedSignature := Base64Encode(HMACSHA256(signedPayload, merchantSecretKey))
"""

from __future__ import annotations

import base64
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
    ContactField,
    CurrencyUnknownError,
    Money,
    PaymentsNotConfiguredError,
    SignatureInvalidError,
    UnhandledBillingEventError,
    build_payment_processor,
)
from nexovue_api.billing.cashfree import (
    API_VERSION,
    CLIENT_ID_HEADER,
    CLIENT_SECRET_HEADER,
    DELIVERY_HEADER,
    ORG_TAG,
    PLAN_TAG,
    SECURE_URL_SCHEME,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    CashfreePaymentProcessor,
    CheckoutHandoffUndecidedError,
)
from nexovue_api.settings import DomesticPaymentSettings

SECRET: Final = "test_secret_key_never_a_real_one"
OTHER_SECRET: Final = "another_accounts_secret_key"
BASE_URL: Final = "https://sandbox.example.test/pg"

SETTINGS: Final = DomesticPaymentSettings(
    provider="cashfree",
    app_id="TEST_APP_ID",
    secret_key=SECRET,
    environment="sandbox",
    base_url=BASE_URL,
)

ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-000000000001")
SUBSCRIPTION_REF: Final = "nexovue-sub-0198f2a0"
DELIVERY_ID: Final = "6f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"
TIMESTAMP: Final = "1757318400000"


def _payload(
    *,
    event_type: str = "SUBSCRIPTION_STATUS_CHANGED",
    subscription_status: str = "ACTIVE",
    tags: dict[str, str] | None = None,
) -> bytes:
    """One Cashfree webhook, shaped as the documented 2026-01-01 samples are."""
    document: dict[str, Any] = {
        "data": {
            "subscription_id": SUBSCRIPTION_REF,
            "cf_subscription_id": "23639356",
            "subscription_details": {
                "cf_subscription_id": "23639356",
                "subscription_id": SUBSCRIPTION_REF,
                "subscription_status": subscription_status,
                "subscription_tags": (
                    {ORG_TAG: str(ORG_ID), PLAN_TAG: "pro"} if tags is None else tags
                ),
                "next_schedule_date": None,
            },
        },
        "event_time": "2026-09-08T10:31:35+05:30",
        "type": event_type,
    }
    return json.dumps(document).encode("utf-8")


def sign(body: bytes, *, timestamp: str = TIMESTAMP, secret: str = SECRET) -> str:
    """What Cashfree puts in the header, computed the way Cashfree computes it."""
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + body, hashlib.sha256)
    return base64.b64encode(digest.digest()).decode("ascii")


def _headers(body: bytes, **overrides: str) -> dict[str, str]:
    headers = {
        TIMESTAMP_HEADER: TIMESTAMP,
        SIGNATURE_HEADER: sign(body),
        DELIVERY_HEADER: DELIVERY_ID,
    }
    headers.update(overrides)
    return headers


COMPLETE_CONTACT: Final = BillingContact(
    email="someone@example.test", name="Some One", phone="+919000000000"
)
AUTHORISED_MAXIMUM: Final = Money(minor_units=1800, currency="USD")
RETURN_URL: Final = "https://app.example.test/settings/billing"


async def _checkout(
    *,
    contact: BillingContact = COMPLETE_CONTACT,
    authorised_maximum: Money = AUTHORISED_MAXIMUM,
    return_url: str = RETURN_URL,
) -> Any:
    """One `begin_checkout` with every input valid unless a test says otherwise."""
    return await _processor().begin_checkout(
        org_id=ORG_ID,
        plan_slug="pro",
        contact=contact,
        authorised_maximum=authorised_maximum,
        return_url=return_url,
    )


def _processor(handler: Any = None) -> CashfreePaymentProcessor:
    client = (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) if handler is not None else None
    )
    return CashfreePaymentProcessor(SETTINGS, client=client)


# ---------------------------------------------------------------- the signature


class TestSignatureVerification:
    """The header, on its own. Everything this door decides starts here."""

    def test_a_delivery_signed_with_this_accounts_secret_is_accepted(self) -> None:
        body = _payload()

        event = _processor().event_from_webhook(body=body, headers=_headers(body))

        assert event.kind is BillingEventKind.SUBSCRIPTION_ACTIVATED
        assert event.org_id == ORG_ID
        assert event.subscription_ref == SUBSCRIPTION_REF
        assert event.plan_slug == "pro"
        assert event.delivery_id == DELIVERY_ID

    def test_a_body_altered_after_signing_is_refused(self) -> None:
        """The whole point: the signature covers the bytes, not the intent."""
        body = _payload()
        headers = _headers(body)
        tampered = body.replace(b'"pro"', b'"scale"')

        assert tampered != body
        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=tampered, headers=headers)

    def test_a_wrong_signature_is_refused(self) -> None:
        body = _payload()
        forged = base64.b64encode(b"0" * 32).decode("ascii")

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(
                body=body, headers=_headers(body, **{SIGNATURE_HEADER: forged})
            )

    def test_another_accounts_secret_is_refused(self) -> None:
        """A signature is only proof if it is proof of *this* account."""
        body = _payload()
        headers = _headers(body, **{SIGNATURE_HEADER: sign(body, secret=OTHER_SECRET)})

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers=headers)

    def test_a_signature_bound_to_another_timestamp_is_refused(self) -> None:
        """The timestamp is inside what is signed, so moving it must break it."""
        body = _payload()
        headers = _headers(body, **{TIMESTAMP_HEADER: "1757318400999"})

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers=headers)

    @pytest.mark.parametrize(
        "missing", [SIGNATURE_HEADER, TIMESTAMP_HEADER], ids=["no signature", "no timestamp"]
    )
    def test_a_delivery_missing_a_header_the_scheme_needs_is_refused(self, missing: str) -> None:
        body = _payload()
        headers = _headers(body)
        del headers[missing]

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers=headers)

    @pytest.mark.parametrize("presented", ["", "not base64 at all", "±±±", "sha256=deadbeef"])
    def test_a_malformed_signature_is_refused_rather_than_crashing(self, presented: str) -> None:
        """A header is arbitrary bytes from the internet. Non-ASCII in it must be
        a refusal, not the ValueError `compare_digest` raises on a str."""
        body = _payload()

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(
                body=body, headers=_headers(body, **{SIGNATURE_HEADER: presented})
            )

    def test_an_unsigned_delivery_is_refused_before_the_body_is_read(self) -> None:
        """Garbage that is not even JSON must fail as *unsigned*, not as unparseable
        — proof the verification runs first."""
        body = b"{ this is not json"

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers={DELIVERY_HEADER: DELIVERY_ID})

    def test_header_names_are_matched_without_regard_to_case(self) -> None:
        """HTTP field names are case-insensitive and a dict has already lost it."""
        body = _payload()
        headers = {name.upper(): value for name, value in _headers(body).items()}

        assert _processor().event_from_webhook(body=body, headers=headers).org_id == ORG_ID


class TestReplayProtection:
    def test_a_delivery_with_no_delivery_id_is_refused(self) -> None:
        """Without it a captured delivery cannot be told from a redelivery, and
        the processor redelivers by design. Refusing is the deliberate choice."""
        body = _payload()
        headers = _headers(body)
        del headers[DELIVERY_HEADER]

        with pytest.raises(SignatureInvalidError):
            _processor().event_from_webhook(body=body, headers=headers)

    def test_the_delivery_id_travels_onto_the_event(self) -> None:
        body = _payload()

        event = _processor().event_from_webhook(body=body, headers=_headers(body))

        assert event.delivery_id == DELIVERY_ID


# --------------------------------------------------------------- the translation


class TestTranslation:
    """Cashfree's events, said in this product's words. Nothing of the original
    may reach a caller — that is the whole reason the port names a BillingEvent."""

    @pytest.mark.parametrize(
        "event_type,subscription_status,expected",
        [
            ("SUBSCRIPTION_STATUS_CHANGED", "ACTIVE", BillingEventKind.SUBSCRIPTION_ACTIVATED),
            (
                "SUBSCRIPTION_STATUS_CHANGED",
                "CANCELLED",
                BillingEventKind.SUBSCRIPTION_CANCELLED,
            ),
            (
                "SUBSCRIPTION_STATUS_CHANGED",
                "CUSTOMER_CANCELLED",
                BillingEventKind.SUBSCRIPTION_CANCELLED,
            ),
            ("SUBSCRIPTION_PAYMENT_SUCCESS", "ACTIVE", BillingEventKind.PAYMENT_SUCCEEDED),
            ("SUBSCRIPTION_PAYMENT_FAILED", "ACTIVE", BillingEventKind.PAYMENT_FAILED),
        ],
    )
    def test_an_event_this_product_acts_on_becomes_its_member(
        self, event_type: str, subscription_status: str, expected: BillingEventKind
    ) -> None:
        body = _payload(event_type=event_type, subscription_status=subscription_status)

        event = _processor().event_from_webhook(body=body, headers=_headers(body))

        assert event.kind is expected

    @pytest.mark.parametrize(
        "event_type,subscription_status",
        [
            ("SUBSCRIPTION_STATUS_CHANGED", "BANK_APPROVAL_PENDING"),
            ("SUBSCRIPTION_STATUS_CHANGED", "ON_HOLD"),
            ("SUBSCRIPTION_CARD_EXPIRY_REMINDER", "ACTIVE"),
            ("SUBSCRIPTION_REFUND_STATUS", "ACTIVE"),
            ("", "ACTIVE"),
        ],
    )
    def test_an_event_with_no_member_is_dropped_rather_than_invented(
        self, event_type: str, subscription_status: str
    ) -> None:
        """`BillingEventKind` is a closed set. Growing it from a vendor's
        changelog is how a translation layer stops being one."""
        body = _payload(event_type=event_type, subscription_status=subscription_status)

        with pytest.raises(UnhandledBillingEventError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_signed_delivery_naming_no_organization_is_refused(self) -> None:
        body = _payload(tags={PLAN_TAG: "pro"})

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_signed_delivery_naming_no_plan_is_refused(self) -> None:
        body = _payload(tags={ORG_TAG: str(ORG_ID)})

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_signed_delivery_with_an_unreadable_organization_is_refused(self) -> None:
        body = _payload(tags={ORG_TAG: "not-a-uuid", PLAN_TAG: "pro"})

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))

    def test_a_correctly_signed_body_that_is_not_json_is_refused(self) -> None:
        """Signed, so not a forgery — but still not an event. A different failure
        from an unsigned one, and it must not be a crash."""
        body = b"[]"

        with pytest.raises(BillingError):
            _processor().event_from_webhook(body=body, headers=_headers(body))


# ------------------------------------------------------------------- the cancel


class TestCancel:
    async def test_a_cancel_names_the_subscription_and_carries_the_credentials(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"subscription_status": "CANCELLED"})

        await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)

        assert len(captured) == 1
        request = captured[0]
        assert str(request.url) == f"{BASE_URL}/subscriptions/{SUBSCRIPTION_REF}/manage"
        assert request.headers[CLIENT_ID_HEADER] == "TEST_APP_ID"
        assert request.headers[CLIENT_SECRET_HEADER] == SECRET
        assert request.headers["x-api-version"] == API_VERSION
        assert json.loads(request.content) == {
            "subscription_id": SUBSCRIPTION_REF,
            "action": "CANCEL",
        }

    async def test_cancelling_an_already_cancelled_subscription_is_not_an_error(self) -> None:
        """A customer pressing twice must not see a failure. Decided by asking
        the processor for the status, not by pattern-matching its error text."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            if request.method == "POST":
                return httpx.Response(409, json={"message": "subscription is already cancelled"})
            return httpx.Response(200, json={"subscription_status": "CUSTOMER_CANCELLED"})

        await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)

        assert calls == ["POST", "GET"]

    async def test_a_refused_cancel_on_a_live_subscription_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(500, json={"message": "gateway unavailable"})
            return httpx.Response(200, json={"subscription_status": "ACTIVE"})

        with pytest.raises(BillingError):
            await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)

    async def test_an_unreachable_processor_is_reported_as_such(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(BillingError, match="could not reach"):
            await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)


# ------------------------------------------------------- configuration and wiring


class TestConfigurationAndWiring:
    def test_the_composition_root_answers_with_something_that_fits_the_port(self) -> None:
        processor = build_payment_processor(SETTINGS)

        for method in ("begin_checkout", "cancel", "event_from_webhook"):
            assert callable(getattr(processor, method))

    def test_a_processor_cannot_be_built_without_a_signing_key(self) -> None:
        """A half-filled configuration fails at the first signature check, and a
        mismatching signature is the failure nobody debugs by reading it."""
        with pytest.raises(PaymentsNotConfiguredError, match="PAYMENTS_DOMESTIC_SECRET_KEY"):
            CashfreePaymentProcessor(
                DomesticPaymentSettings(
                    provider="cashfree",
                    app_id="TEST_APP_ID",
                    secret_key="",
                    environment="sandbox",
                    base_url=BASE_URL,
                )
            )

    def test_the_processor_declares_the_contact_it_needs(self) -> None:
        """The phone is the one that costs: this processor requires it on the
        create call and collects none on its hosted page, where others ask the
        payer directly. Declared rather than assumed by the port — ADR 0109."""
        required = _processor().required_contact_fields()

        assert required == {ContactField.NAME, ContactField.EMAIL, ContactField.PHONE}

    async def test_a_contact_missing_a_declared_field_is_refused_here_not_at_the_provider(
        self,
    ) -> None:
        """The screen renders what `required_contact_fields` names, so this
        should be unreachable — which is exactly why it is worth a test. A caller
        that skips one learns which, rather than reading a provider's rejection."""
        without_phone = BillingContact(email="someone@example.test", name="Some One")

        with pytest.raises(BillingError, match="phone"):
            await _checkout(contact=without_phone)

    @pytest.mark.parametrize("minor_units", [0, -600], ids=["nothing", "negative"])
    async def test_a_mandate_for_nothing_is_refused(self, minor_units: int) -> None:
        """A mandate authorising no charge is not a subscription anybody bought,
        and a negative one is arithmetic that went wrong upstream."""
        with pytest.raises(BillingError, match="chargeable amount"):
            await _checkout(authorised_maximum=Money(minor_units=minor_units, currency="USD"))

    async def test_an_amount_in_an_unnameable_currency_is_refused_before_the_wire(self) -> None:
        """This processor takes major units, so an amount whose smallest unit
        this product cannot name would have to be divided by a guessed number.
        The yen's minor unit is the yen: a default of a hundred bills 1% of it."""
        with pytest.raises(CurrencyUnknownError, match="JPY"):
            await _checkout(authorised_maximum=Money(minor_units=600, currency="JPY"))

    @pytest.mark.parametrize(
        "return_url",
        ["http://localhost:5173/settings/billing", "app.example.test/settings/billing"],
        ids=["plain http", "no scheme"],
    )
    async def test_a_return_url_the_processor_would_never_register_is_refused(
        self, return_url: str
    ) -> None:
        """Their go-live rules take https only, and a link refused at
        registration is found months after the line that built it was written.
        https://www.cashfree.com/docs/payments/online/go-live/whitelist"""
        with pytest.raises(BillingError, match=SECURE_URL_SCHEME):
            await _checkout(return_url=return_url)

    async def test_checkout_refuses_because_no_call_carries_both_a_url_and_the_tags(
        self,
    ) -> None:
        """Every input is now supplied and it still cannot start one.

        Checked against the documentation on 2026-09-08, not remembered: the
        create call that accepts `subscription_tags` answers with a session id
        for a browser SDK, and the one that answers with a link accepts no tags.
        `_translate` reads the organization out of those tags, so satisfying
        `Checkout.url` and `event_from_webhook` at once is not currently
        possible — which is a decision about ADR 0102's port, above this file.
        """
        with pytest.raises(CheckoutHandoffUndecidedError, match="tags"):
            await _checkout()

    async def test_no_failure_message_carries_the_signing_key_or_the_raw_body(self) -> None:
        """Every one of these strings ends up in a log line or a problem
        document. A secret, a signature or a raw body in one of them is the leak
        that never gets noticed, because nothing fails when it happens."""
        body = _payload()
        messages: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "authentication Failed"})

        with pytest.raises(SignatureInvalidError) as refused:
            _processor().event_from_webhook(
                body=body, headers=_headers(body, **{SIGNATURE_HEADER: "wrong"})
            )
        messages.append(str(refused.value))

        with pytest.raises(BillingError) as failed:
            await _processor(handler).cancel(subscription_ref=SUBSCRIPTION_REF)
        messages.append(str(failed.value))

        for message in messages:
            assert SECRET not in message
            assert sign(body) not in message
            assert body.decode() not in message
        # Guards the check itself: an empty list would pass everything above.
        assert len(messages) == 2
