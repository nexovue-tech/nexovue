"""Billing logic and its tenancy, offline (P1-73).

Three things are proved here, and they are the three that make an
unauthenticated money endpoint safe to expose.

**Nothing unproved is written.** An unsigned delivery is refused and the store is
asserted to be *empty afterwards*, not merely that an exception came out.

**A delivery cannot be replayed.** The processor delivers at least once by
design, so this is ordinary traffic rather than an attack, and the second
arrival must change nothing and still answer 200 — anything else asks for the
same event forever.

**Every query carries its tenant in the statement.** The statements are captured
before they reach a database and their bound parameters inspected. A query that
lost its `org_id` predicate would still return the right rows against a real
Postgres holding one tenant's data, so asserting on the *statement* is what
proves the filter is there.

`test_payments.py` covers the processor itself, including the signature scheme.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.billing import (
    BillingError,
    BillingEvent,
    BillingEventKind,
    CancellationOutcome,
    Checkout,
    ConfiguredProcessor,
    ContactField,
    CurrencyUnknownError,
    HostedPage,
    Money,
    SignatureInvalidError,
    UnhandledBillingEventError,
    queries,
    service,
)
from nexovue_api.billing.razorpay import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    RazorpayPaymentProcessor,
)
from nexovue_api.billing.schemas import CheckoutStyle, DeliveryOutcome, StartCheckoutRequest
from nexovue_api.data.postgres.models import (
    BillingSubscription,
    BillingWebhookDelivery,
    Organization,
    Plan,
    PlanPrice,
    UsageDailyService,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    UnauthenticatedError,
)
from nexovue_api.settings import (
    API_PREFIX,
    DOMESTIC_PAYMENT_VARIABLES,
    DomesticPaymentSettings,
    Settings,
)
from nexovue_api.usage import billing as usage_billing
from nexovue_api.usage import queries as usage_queries

ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-000000000001")
OTHER_ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-0000000000ff")
SUBSCRIPTION_REF: Final = "sub_0198f2a01b2c3d"
DELIVERY_ID: Final = "6f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"
PROCESSOR_NAME: Final = "razorpay"

# The signed-in member, whose address the checkout falls back to when the
# processor asks for one and the screen collected none.
OWNER_EMAIL: Final = "owner@example.test"

# The count the mandate was sized on. Any number works; a distinctive one makes
# a wrong value in an assertion read as wrong rather than as a coincidence.
SERVICES_COMMITTED: Final = 7

SECRET: Final = "test_secret_key_never_a_real_one"
SETTINGS: Final = DomesticPaymentSettings(
    provider="razorpay",
    app_id="TEST_APP_ID",
    secret_key=SECRET,
    environment="sandbox",
    base_url="https://sandbox.example.test/pg",
    webhook_secret=SECRET,
)


# ------------------------------------------------------------------- the doubles


class CapturingSession:
    """Records what it was handed and answers from what a test preloaded.

    Deliberately not a database. The question is what the query asked for and
    what the service staged, and a real store would answer the first with data
    rather than with the query.
    """

    def __init__(self, *, answers: list[Any] | None = None) -> None:
        self.statements: list[Any] = []
        self.staged: list[Any] = []
        self._answers = list(answers or [])

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return self._answers.pop(0) if self._answers else None

    def add(self, instance: Any) -> None:
        self.staged.append(instance)

    @property
    def bound_values(self) -> set[Any]:
        compiled = self.statements[-1].compile()
        return set(compiled.params.values())

    def staged_of(self, kind: type) -> list[Any]:
        return [row for row in self.staged if isinstance(row, kind)]


@dataclass
class StubProcessor:
    """A `PaymentProcessor` that never imports one.

    This class existing at all is what ADR 0102's "Protocol rather than a base
    class" bought: structural typing means a double does not inherit from us.
    """

    event: BillingEvent | None = None
    failure: Exception | None = None
    # Raised by the *call* methods. Separate from `failure`, which is the
    # webhook's: a processor that refuses a checkout and one that sends an
    # unreadable delivery are different failures on different paths.
    refusal: BillingError | None = None
    seen: list[bytes] | None = None
    checkout_url: str = "https://processor.example.test/pay/abc"
    subscription_ref: str = SUBSCRIPTION_REF
    checkouts: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    cancellation: CancellationOutcome = CancellationOutcome.REQUESTED

    def required_contact_fields(self) -> frozenset[ContactField]:
        """An email and nothing else — deliberately *not* the domestic
        processor's three. A double that mirrored one implementation would let a
        screen hard-code that implementation's fields and still pass."""
        return frozenset({ContactField.EMAIL})

    async def begin_checkout(
        self,
        *,
        org_id: uuid.UUID,
        plan_slug: str,
        contact: Any,
        commitment: Any,
        return_url: str,
    ) -> Checkout:
        self.checkouts.append(
            {
                "org_id": org_id,
                "plan_slug": plan_slug,
                "contact": contact,
                "commitment": commitment,
                "return_url": return_url,
            }
        )
        if self.refusal is not None:
            raise self.refusal
        # A hosted page — deliberately *not* the domestic processor's embedded
        # script. A double that mirrored the one real implementation would let a
        # screen that only handles one shape pass every test here.
        return Checkout(
            handoff=HostedPage(url=self.checkout_url),
            subscription_ref=self.subscription_ref,
            processor_customer_ref="cust_1",
        )

    async def cancel(self, *, subscription_ref: str) -> CancellationOutcome:
        if self.refusal is not None:
            raise self.refusal
        self.cancelled.append(subscription_ref)
        return self.cancellation

    def event_from_webhook(self, *, body: bytes, headers: dict[str, str]) -> BillingEvent:
        if self.seen is not None:
            self.seen.append(body)
        if self.failure is not None:
            raise self.failure
        assert self.event is not None
        return self.event


def _event(
    kind: BillingEventKind = BillingEventKind.SUBSCRIPTION_ACTIVATED,
    *,
    subscription_ref: str = SUBSCRIPTION_REF,
) -> BillingEvent:
    """ADR 0110: an event carries no organization and no plan. Both come from
    the row the checkout wrote, which the reference names."""
    return BillingEvent(kind=kind, subscription_ref=subscription_ref, delivery_id=DELIVERY_ID)


def _configured(processor: Any) -> ConfiguredProcessor:
    return ConfiguredProcessor(name=PROCESSOR_NAME, processor=processor)


def _plan_row(slug: str = "pro") -> Plan:
    return Plan(slug=slug, name=slug.title(), raw_retention_days=30, derived_retention_days=395)


def _checkout_request(
    *, plan_slug: str = "pro", services_committed: int = 3, currency: str = "USD"
) -> StartCheckoutRequest:
    return StartCheckoutRequest(
        plan_slug=plan_slug,
        currency=currency,
        services_committed=services_committed,
        contact_name="Some One",
        contact_email="someone@example.test",
        contact_phone="+919000000000",
    )


def _pending(
    *,
    org_id: uuid.UUID = ORG_ID,
    plan_slug: str = "pro",
    status: str = service.STATUS_PENDING,
    currency: str = "USD",
) -> BillingSubscription:
    """The row `begin_plan_change` writes before the customer is redirected.

    ADR 0110 makes it the thing every webhook is resolved against, so a test
    with no such row is testing an event this system has no record of starting.
    """
    return BillingSubscription(
        org_id=org_id,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
        plan_slug=plan_slug,
        status=status,
        services_committed=SERVICES_COMMITTED,
        currency=currency,
    )


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession(answers=[_pending()])


# ------------------------------------------------------------------- the tenancy


async def test_the_replay_check_binds_the_organization(session: CapturingSession) -> None:
    """Unscoped, this would answer "has anyone seen this delivery?" — and the
    delivery id is the processor's, not ours, so the answer would cross tenants."""
    await queries.delivery_already_recorded(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        delivery_id=DELIVERY_ID,
    )

    assert ORG_ID in session.bound_values
    assert DELIVERY_ID in session.bound_values


async def test_the_subscription_lookup_binds_the_organization(session: CapturingSession) -> None:
    """A lookup by reference alone would let one tenant's webhook move another
    tenant's arrangement, if two processors ever minted the same string."""
    await queries.subscription_by_ref(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
    )

    assert ORG_ID in session.bound_values
    assert SUBSCRIPTION_REF in session.bound_values


async def test_the_latest_subscription_lookup_binds_the_organization(
    session: CapturingSession,
) -> None:
    """The billing screen's own read. Unscoped it would answer "whose is the
    newest arrangement in the system?" and show it under this tenant's plan."""
    await queries.latest_subscription(session, org_id=ORG_ID)  # type: ignore[arg-type]

    assert ORG_ID in session.bound_values


async def test_no_query_binds_an_organization_it_was_not_given(
    session: CapturingSession,
) -> None:
    """Guards the tests above: presence would pass even if org_id were ignored."""
    await queries.subscription_by_ref(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
    )

    assert OTHER_ORG_ID not in session.bound_values


# ------------------------------------------------------------------- the refusals


async def test_an_unsigned_delivery_writes_nothing(session: CapturingSession) -> None:
    """The assertion that matters is the empty store, not the exception."""
    configured = _configured(StubProcessor(failure=SignatureInvalidError("refused")))

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=configured,
            body=b"{}",
            headers={},
        )

    assert session.staged == []


async def test_the_refusal_names_no_account_and_no_cause(session: CapturingSession) -> None:
    """Saying which of "unsigned" and "wrongly signed" it was would tell a forger
    how close they got."""
    configured = _configured(StubProcessor(failure=SignatureInvalidError("refused")))

    with pytest.raises(UnauthenticatedError) as refused:
        await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=configured,
            body=b"{}",
            headers={},
        )

    assert refused.value.detail == service.REFUSED_DETAIL
    assert PROCESSOR_NAME not in refused.value.detail


async def test_an_event_this_product_does_not_act_on_is_accepted_and_stored_nowhere(
    session: CapturingSession,
) -> None:
    """A 200 with nothing written. Any other answer asks the processor to
    redeliver an event that will be dropped again on every attempt."""
    configured = _configured(StubProcessor(failure=UnhandledBillingEventError("a refund")))

    acknowledgement = await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert acknowledgement.outcome is DeliveryOutcome.IGNORED
    assert session.staged == []


async def test_a_replayed_delivery_changes_nothing() -> None:
    """The second arrival of the same delivery id must move no state."""
    already_seen = CapturingSession(answers=[_pending(), uuid.uuid4()])
    configured = _configured(StubProcessor(event=_event()))

    acknowledgement = await service.record_from_webhook(
        already_seen,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert acknowledgement.outcome is DeliveryOutcome.DUPLICATE
    assert already_seen.staged == []


# ------------------------------------------------------------------- what is kept


async def test_an_activation_activates_the_pending_row_and_records_the_delivery() -> None:
    """The arrangement is not created here — it was created by the checkout.

    ADR 0110: the event carries no organization, so an activation can only ever
    move a row that already exists. What it proves is that the delivery lands on
    the tenant the *checkout* named, not one the processor did.
    """
    pending = _pending()
    session = CapturingSession(answers=[pending, None, _organization()])
    configured = _configured(StubProcessor(event=_event()))

    acknowledgement = await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert acknowledgement.outcome is DeliveryOutcome.RECORDED
    assert pending.status == service.STATUS_ACTIVE
    assert pending.activated_at is not None
    assert session.staged_of(BillingSubscription) == []

    delivery = session.staged_of(BillingWebhookDelivery)[0]
    assert delivery.org_id == ORG_ID
    assert delivery.delivery_id == DELIVERY_ID
    assert delivery.kind == BillingEventKind.SUBSCRIPTION_ACTIVATED.value


async def test_an_event_naming_a_subscription_no_checkout_started_is_not_attributed() -> None:
    """The refusal ADR 0110 turns on. Signed, so not a forgery — and about an
    arrangement this system has no record of starting, which no amount of
    payload gives an honest owner. Acknowledged so it stops being redelivered,
    and nothing is written."""
    session = CapturingSession(answers=[None])
    configured = _configured(StubProcessor(event=_event()))

    acknowledgement = await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert acknowledgement.outcome is DeliveryOutcome.IGNORED
    assert session.staged == []


async def test_the_stored_delivery_holds_no_body_no_signature_and_no_vendor_name(
    session: CapturingSession,
) -> None:
    """The raw body is the thing the signature covers and the thing that must
    never be persisted or logged; the vendor's event name is the thing the port
    exists to keep out of everything above it."""
    body = json.dumps({"type": "SUBSCRIPTION_STATUS_CHANGED", "secret": "x"}).encode()
    configured = _configured(StubProcessor(event=_event()))

    await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=body,
        headers={SIGNATURE_HEADER: "a-signature"},
    )

    delivery = session.staged_of(BillingWebhookDelivery)[0]
    stored = {
        column.name: getattr(delivery, column.name, None)
        for column in BillingWebhookDelivery.__table__.columns
    }
    written = " ".join(str(value) for value in stored.values())
    assert body.decode() not in written
    assert "a-signature" not in written
    assert "SUBSCRIPTION_STATUS_CHANGED" not in written


async def test_a_cancellation_ends_the_arrangement_it_names() -> None:
    live = BillingSubscription(
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
        plan_slug="pro",
        status=service.STATUS_ACTIVE,
        services_committed=SERVICES_COMMITTED,
    )
    session = CapturingSession(answers=[live, None])
    configured = _configured(StubProcessor(event=_event(BillingEventKind.SUBSCRIPTION_CANCELLED)))

    await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert live.status == service.STATUS_CANCELLED
    assert live.cancelled_at is not None
    # Not re-created: the row it found is the row it changed.
    assert session.staged_of(BillingSubscription) == []


@pytest.mark.parametrize(
    "kind", [BillingEventKind.PAYMENT_SUCCEEDED, BillingEventKind.PAYMENT_FAILED]
)
async def test_a_charge_does_not_change_what_the_arrangement_is(
    kind: BillingEventKind,
) -> None:
    """A card that declines once must not cancel a customer. Only the processor's
    own cancellation event ends a subscription."""
    live = BillingSubscription(
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
        plan_slug="pro",
        status=service.STATUS_ACTIVE,
        services_committed=SERVICES_COMMITTED,
    )
    session = CapturingSession(answers=[live, None])

    await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=_configured(StubProcessor(event=_event(kind))),
        body=b"{}",
        headers={},
    )

    assert live.status == service.STATUS_ACTIVE
    assert live.cancelled_at is None


async def test_an_already_active_arrangement_keeps_its_first_activation_date() -> None:
    """A re-activation after a hold is not the day the customer started paying,
    and an invoice question asks for the first one."""
    first_activation = datetime(2026, 1, 1, tzinfo=UTC)
    live = BillingSubscription(
        org_id=ORG_ID,
        processor=PROCESSOR_NAME,
        subscription_ref=SUBSCRIPTION_REF,
        plan_slug="pro",
        status=service.STATUS_ACTIVE,
        services_committed=SERVICES_COMMITTED,
        activated_at=first_activation,
    )
    session = CapturingSession(answers=[live, None])

    await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=_configured(StubProcessor(event=_event())),
        body=b"{}",
        headers={},
    )

    assert live.activated_at == first_activation


# --------------------------------------------------------- signature to stored row


def _signed(body: bytes) -> dict[str, str]:
    """The processor's scheme: hex over the raw bytes, webhook secret, no
    timestamp in the digest."""
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256)
    return {
        SIGNATURE_HEADER: digest.hexdigest(),
        DELIVERY_HEADER: DELIVERY_ID,
    }


def _delivery_body() -> bytes:
    return json.dumps(
        {
            "event": "subscription.activated",
            "payload": {"subscription": {"entity": {"id": SUBSCRIPTION_REF, "status": "active"}}},
        }
    ).encode()


async def test_a_real_signed_delivery_reaches_a_row_and_a_tampered_one_does_not() -> None:
    """End to end through the real processor: bytes in, a row staged — and the
    same bytes with one character changed reach nothing at all."""
    body = _delivery_body()
    headers = _signed(body)
    configured = _configured(RazorpayPaymentProcessor(SETTINGS))

    accepted = CapturingSession(answers=[_pending(), None, _organization()])
    outcome = await service.record_from_webhook(
        accepted,  # type: ignore[arg-type]
        configured=configured,
        body=body,
        headers=headers,
    )
    assert outcome.outcome is DeliveryOutcome.RECORDED
    assert accepted.staged_of(BillingWebhookDelivery)[0].org_id == ORG_ID

    tampered_body = body.replace(b"activated", b"cancelled")
    refused = CapturingSession(answers=[_pending()])
    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            refused,  # type: ignore[arg-type]
            configured=configured,
            body=tampered_body,
            headers=headers,
        )
    assert refused.staged == []


# ------------------------------------------------------------------ the settings


class TestSettings:
    """All of the payment variables or none, `EmailSettings`' rule."""

    @pytest.fixture(autouse=True)
    def _isolated_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start from an environment carrying none of the payment variables.

        `Settings` reads the environment for anything the caller does not pass,
        and `just` loads `.env` before every recipe — so without this a test
        asserting "a half-filled configuration is None" passes on a bare shell
        and fails in the gate, which is where it was found. The same fixture and
        the same reason live in `test_settings.py`.
        """
        for name in DOMESTIC_PAYMENT_VARIABLES:
            monkeypatch.delenv(name, raising=False)

    def test_payments_are_unconfigured_until_every_variable_is_set(self) -> None:
        partly = Settings(
            env="development",
            postgres_dsn="postgresql://nexovue:secret@localhost:5432/nexovue",
            clickhouse_dsn="clickhouse://nexovue:secret@localhost:8123/nexovue",
            redis_url="redis://localhost:6379/0",
            payments_domestic_app_id="TEST_APP_ID",
        )

        assert partly.domestic_payments is None

    def test_a_complete_configuration_is_handed_over_whole(self) -> None:
        complete = Settings(
            env="development",
            postgres_dsn="postgresql://nexovue:secret@localhost:5432/nexovue",
            clickhouse_dsn="clickhouse://nexovue:secret@localhost:8123/nexovue",
            redis_url="redis://localhost:6379/0",
            payments_domestic_app_id="TEST_APP_ID",
            payments_domestic_secret_key=SECRET,
            payments_domestic_base_url="https://sandbox.example.test/pg/",
        )

        configured = complete.domestic_payments
        assert configured is not None
        assert configured.app_id == "TEST_APP_ID"
        assert configured.secret_key == SECRET
        # A trailing slash would double every path, which some gateways answer
        # with a redirect and others with a 404.
        assert configured.base_url == "https://sandbox.example.test/pg"


# ------------------------------------------------------------------- the door


def _build_app(session: CapturingSession) -> Any:
    """The real app, with Postgres and the settings replaced.

    What is under test is the wiring — the raw body reaching the verifier, the
    headers reaching the adapter, and the status codes — not the stores.
    """
    # Imported here so `environment` has already populated the configuration
    # `main` reads when it builds its module-level app.
    from nexovue_api.billing.dependencies import get_domestic_payments
    from nexovue_api.deps import get_app_settings, get_db_session
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_domestic_payments] = lambda: _configured(
        RazorpayPaymentProcessor(SETTINGS)
    )
    return app


async def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_the_door_accepts_a_signed_delivery_at_the_role_named_path() -> None:
    """A URL pasted into somebody else's dashboard. Naming the role rather than
    the vendor is what lets the second processor have its own."""
    body = _delivery_body()
    session = CapturingSession(answers=[_pending(), None, _organization()])

    async with await _client(_build_app(session)) as client:
        response = await client.post(
            f"{API_PREFIX}/billing/webhook/domestic", content=body, headers=_signed(body)
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == DeliveryOutcome.RECORDED.value
    assert session.staged_of(BillingWebhookDelivery)[0].org_id == ORG_ID


async def test_the_door_refuses_a_tampered_delivery_without_writing() -> None:
    """The body is read as raw bytes: a pydantic body parameter would have
    FastAPI answer 422 on a forgery before the signature was ever looked at."""
    body = _delivery_body()
    headers = _signed(body)
    session = CapturingSession(answers=[_pending()])

    async with await _client(_build_app(session)) as client:
        response = await client.post(
            f"{API_PREFIX}/billing/webhook/domestic",
            content=body.replace(b"activated", b"cancelled"),
            headers=headers,
        )

    assert response.status_code == 401
    assert session.staged == []


async def test_the_customer_returns_by_post_and_is_sent_to_the_billing_screen() -> None:
    """The processor returns a customer by POST. Pointing that at the single-page
    app's own path put a 404 in front of somebody who had just paid, because no
    host serving a SPA answers a POST to a client route — the dev server falls
    through and a static host refuses. 303, so the browser follows it as a GET.
    """
    async with await _client(_build_app(CapturingSession(answers=[]))) as client:
        response = await client.post(
            f"{API_PREFIX}/billing/checkout/return",
            data={"razorpay_subscription_id": SUBSCRIPTION_REF, "razorpay_signature": "x"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == service.BILLING_SCREEN_PATH


async def test_the_return_needs_no_session_and_writes_nothing() -> None:
    """The browser arrives from the processor's origin, so a SameSite cookie is
    not sent: requiring one would answer the paying customer with a 401. It
    reads no body either — the subscription moves on the signed webhook."""
    session = CapturingSession(answers=[])

    async with await _client(_build_app(session)) as client:
        response = await client.post(f"{API_PREFIX}/billing/checkout/return")

    assert response.status_code == 303
    assert session.staged == []


async def test_a_refreshed_return_lands_on_the_screen_rather_than_a_405() -> None:
    """A back button or a reload re-issues the landing as a GET."""
    async with await _client(_build_app(CapturingSession(answers=[]))) as client:
        response = await client.get(f"{API_PREFIX}/billing/checkout/return")

    assert response.status_code == 303
    assert response.headers["location"] == service.BILLING_SCREEN_PATH


# --------------------------------------------------- the plan change (P1-74)


ORGANIZATION: Final = "organization"


def _organization(plan: str = "free") -> Organization:
    """A tenant row, built without a session. `id` is set by hand because
    nothing here reaches a database to have a default applied."""
    organization = Organization(id=ORG_ID, slug="acme", name="Acme", plan=plan)
    return organization


def _delivery_answers(organization: Organization | None) -> list[Any]:
    """What `record_from_webhook` asks the store for, in the order it asks.

    Three scalars, in order: which arrangement this reference names, whether
    this delivery has been seen, and the organization that arrangement belongs
    to. The reference resolves first — ADR 0110 — because it is what establishes
    the tenant the other two are asked about.
    """
    return [_pending(), None, organization]


class TestThePlanFollowsTheProcessor:
    """P1-74's answer, and the property the whole row exists for: no route sets
    `organizations.plan`, so a tier is only ever granted by an event a processor
    signed."""

    async def test_an_activation_puts_the_organization_on_the_plan_it_paid_for(self) -> None:
        """Within one `RECEIVER_ALLOWANCE_CACHE_TTL` of this write, the ingest
        door stops refusing — which is what "upgrade must take effect on the
        ingest path" means in the plan."""
        organization = _organization(plan="free")
        session = CapturingSession(answers=_delivery_answers(organization))

        outcome = await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=_configured(StubProcessor(event=_event())),
            body=b"{}",
            headers={},
        )

        assert outcome.outcome is DeliveryOutcome.RECORDED
        assert organization.plan == "pro"

    async def test_a_cancellation_drops_the_organization_back_to_the_free_tier(self) -> None:
        """At the processor's own period boundary, not on a date computed here:
        this runs when the processor's event says the arrangement ended."""
        organization = _organization(plan="pro")
        session = CapturingSession(answers=_delivery_answers(organization))

        await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=_configured(
                StubProcessor(event=_event(BillingEventKind.SUBSCRIPTION_CANCELLED))
            ),
            body=b"{}",
            headers={},
        )

        assert organization.plan == service.PLAN_WITHOUT_A_SUBSCRIPTION

    @pytest.mark.parametrize(
        "kind",
        [BillingEventKind.PAYMENT_SUCCEEDED, BillingEventKind.PAYMENT_FAILED],
        ids=["succeeded", "failed"],
    )
    async def test_a_charge_moves_no_plan(self, kind: BillingEventKind) -> None:
        """A card that declines once must not cost a customer their tier, and a
        successful charge on a plan they already hold changes nothing."""
        organization = _organization(plan="pro")
        session = CapturingSession(answers=_delivery_answers(organization))

        await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=_configured(StubProcessor(event=_event(kind))),
            body=b"{}",
            headers={},
        )

        assert organization.plan == "pro"

    async def test_an_event_for_an_organization_that_is_gone_is_still_accepted(self) -> None:
        """The delivery was signed, so refusing it asks the processor to send it
        again forever. A deleted tenant is not a reason to do that."""
        session = CapturingSession(answers=_delivery_answers(None))

        outcome = await service.record_from_webhook(
            session,  # type: ignore[arg-type]
            configured=_configured(StubProcessor(event=_event())),
            body=b"{}",
            headers={},
        )

        assert outcome.outcome is DeliveryOutcome.RECORDED


# ----------------------------------------------- what a mandate may charge


_PRO_PRICE: Final = PlanPrice(
    plan_slug="pro",
    effective_from=date(2026, 9, 1),
    currency="USD",
    price_per_service_minor_units=600,
    max_billed_services=None,
    included_bytes_per_service=10 * 1024**3,
    browser_share_percent=None,
)

_FREE_PRICE: Final = PlanPrice(
    plan_slug="free",
    effective_from=date(2026, 9, 1),
    currency="USD",
    price_per_service_minor_units=0,
    max_billed_services=3,
    included_bytes_per_service=2 * 1024**3,
    browser_share_percent=25,
)

TODAY: Final = date(2026, 9, 8)


class TestTheCommitment:
    """ADR 0107's ceiling, in money, from the row the usage screen already reads."""

    async def test_the_commitment_carries_the_rate_and_the_count_separately(self) -> None:
        """Not a multiplied total. One processor wants a ceiling for the whole
        mandate; another wants a per-unit plan and a quantity, and prints
        "399.00 x 5 units" from exactly that. A total cannot be divided back out
        by an adapter that does not know the divisor.
        """
        session = CapturingSession(answers=[_PRO_PRICE])

        commitment = await service.commitment_for(
            session,  # type: ignore[arg-type]
            plan_slug="pro",
            currency="USD",
            services_committed=3,
            on_day=TODAY,
        )

        assert commitment.services == 3
        assert commitment.rate == Money(minor_units=600, currency="USD")

    async def test_a_month_is_the_rate_times_the_count_in_minor_units(self) -> None:
        """Three services at $6 is $18, and $18 is 1800 — never 18.0. Money is a
        whole count of the smallest unit, because binary floating point cannot
        hold a tenth and an amount that drifts is an amount a customer disputes.
        """
        session = CapturingSession(answers=[_PRO_PRICE])

        commitment = await service.commitment_for(
            session,  # type: ignore[arg-type]
            plan_slug="pro",
            currency="USD",
            services_committed=3,
            on_day=TODAY,
        )

        assert commitment.total() == Money(minor_units=1800, currency="USD")
        assert isinstance(commitment.total().minor_units, int)

    async def test_the_rate_is_the_one_in_force_on_the_day(self) -> None:
        """ADR 0083 appends price rows with an effective date, so a repricing
        next month must not change a mandate signed this month."""
        session = CapturingSession(answers=[_PRO_PRICE])

        commitment = await service.commitment_for(
            session,  # type: ignore[arg-type]
            plan_slug="pro",
            currency="USD",
            services_committed=7,
            on_day=TODAY,
        )

        assert commitment.rate.minor_units == _PRO_PRICE.price_per_service_minor_units

    async def test_a_tier_with_no_list_price_cannot_be_subscribed_to(self) -> None:
        """`enterprise` is priced by agreement and has no row on purpose.
        Inventing one would authorise a mandate against a number no contract uses."""
        session = CapturingSession(answers=[None])

        with pytest.raises(InvalidRequestError, match="no list price"):
            await service.commitment_for(
                session,  # type: ignore[arg-type]
                plan_slug="enterprise",
                currency="USD",
                services_committed=3,
                on_day=TODAY,
            )

    async def test_a_free_tier_has_no_mandate_to_authorise(self) -> None:
        session = CapturingSession(answers=[_FREE_PRICE])

        with pytest.raises(InvalidRequestError, match="costs nothing"):
            await service.commitment_for(
                session,  # type: ignore[arg-type]
                plan_slug="free",
                currency="USD",
                services_committed=3,
                on_day=TODAY,
            )


class TestTheReturnUrl:
    def test_it_points_at_an_endpoint_and_carries_no_outcome(self) -> None:
        """The webhook is what this product believes. A return URL carrying a
        result would be a result the customer could type into the address bar.

        It names an API route, not a screen: the processor returns the customer
        by POST, and only a server answers one.
        """
        url = service.checkout_return_url("https://app.example.test/")

        assert url == "https://app.example.test/api/v1/billing/checkout/return"
        assert "?" not in url

    def test_it_is_not_a_client_route(self) -> None:
        """The bug this shape exists for: a POST to the single-page app's own
        path is served by nothing, so the customer met a 404 after paying."""
        assert not service.checkout_return_url("https://app.example.test").endswith(
            service.BILLING_SCREEN_PATH
        )

    def test_an_unset_app_base_url_is_refused_rather_than_guessed(self) -> None:
        """A guessed host is a link to nowhere, met after the money has moved."""
        with pytest.raises(InvalidRequestError, match="APP_BASE_URL"):
            service.checkout_return_url("   ")


class TestStartingACheckout:
    async def test_the_processor_is_handed_the_resolved_price_and_return_url(self) -> None:
        """The two inputs `begin_checkout` used to refuse for, resolved above the
        adapter: the price out of `plan_prices` and the URL out of APP_BASE_URL."""
        processor = StubProcessor()
        # find_plan, latest_subscription, find_effective_price.
        session = CapturingSession(answers=[_plan_row(), None, _PRO_PRICE])

        handoff = await service.begin_plan_change(
            session,  # type: ignore[arg-type]
            configured=_configured(processor),
            organization=_organization(),
            billing_email=OWNER_EMAIL,
            request=_checkout_request(services_committed=3),
            app_base_url="https://app.example.test",
            today=TODAY,
        )

        assert handoff.style is CheckoutStyle.HOSTED_PAGE
        assert handoff.url == processor.checkout_url
        commitment = processor.checkouts[0]["commitment"]
        assert commitment.services == 3
        assert commitment.rate == Money(600, "USD")
        assert commitment.total() == Money(1800, "USD")
        assert (
            processor.checkouts[0]["return_url"]
            == "https://app.example.test/api/v1/billing/checkout/return"
        )
        assert processor.checkouts[0]["org_id"] == ORG_ID

    async def test_the_contact_reaches_the_processor_and_is_never_staged(self) -> None:
        """ADR 0109: the processor holds what the bank's notice needs and we hold
        no copy. Nothing carrying a phone number may be staged for the store."""
        processor = StubProcessor()
        session = CapturingSession(answers=[_plan_row(), None, _PRO_PRICE])

        await service.begin_plan_change(
            session,  # type: ignore[arg-type]
            configured=_configured(processor),
            organization=_organization(),
            billing_email=OWNER_EMAIL,
            request=_checkout_request(),
            app_base_url="https://app.example.test",
            today=TODAY,
        )

        assert processor.checkouts[0]["contact"].phone == "+919000000000"

        # A row is staged now — ADR 0110 — so "never staged" is checked against
        # what that row holds rather than against there being no row at all.
        staged = session.staged_of(BillingSubscription)[0]
        written = " ".join(
            str(getattr(staged, column.name, None))
            for column in BillingSubscription.__table__.columns
        )
        assert "+919000000000" not in written
        assert "someone@example.test" not in written
        assert "Some One" not in written

    async def test_an_unknown_plan_is_refused_before_a_processor_is_called(self) -> None:
        processor = StubProcessor()
        session = CapturingSession(answers=[None])

        with pytest.raises(InvalidRequestError, match="No such plan"):
            await service.begin_plan_change(
                session,  # type: ignore[arg-type]
                configured=_configured(processor),
                organization=_organization(),
                billing_email=OWNER_EMAIL,
                request=_checkout_request(plan_slug="platinum"),
                app_base_url="https://app.example.test",
                today=TODAY,
            )
        assert processor.checkouts == []

    async def test_a_second_mandate_for_a_plan_already_live_is_refused(self) -> None:
        """Two mandates for one plan is two monthly debits for one subscription."""
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor()
        session = CapturingSession(answers=[_plan_row(), live])

        with pytest.raises(ConflictError):
            await service.begin_plan_change(
                session,  # type: ignore[arg-type]
                configured=_configured(processor),
                organization=_organization(plan="pro"),
                billing_email=OWNER_EMAIL,
                request=_checkout_request(),
                app_base_url="https://app.example.test",
                today=TODAY,
            )
        assert processor.checkouts == []


class TestEndingASubscription:
    async def test_the_processor_is_asked_to_cancel_the_arrangement_it_issued(self) -> None:
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor()
        session = CapturingSession(answers=[live])

        await service.end_subscription(
            session,  # type: ignore[arg-type]
            configured=_configured(processor),
            organization=_organization(plan="pro"),
        )

        assert processor.cancelled == [SUBSCRIPTION_REF]

    async def test_an_arrangement_already_over_is_ended_here_and_not_waited_for(self) -> None:
        """The processor was asked and answered "already over", so no event is
        coming. This is the state a customer reaches by cancelling in the
        processor's own dashboard — or by cancelling before anything was
        listening — and waiting for a webhook leaves them on a paid tier forever.
        """
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor(cancellation=CancellationOutcome.ALREADY_ENDED)
        organization = _organization(plan="pro")
        session = CapturingSession(answers=[live, organization])

        await service.end_subscription(
            session,  # type: ignore[arg-type]
            configured=_configured(processor),
            organization=organization,
        )

        assert live.status == service.STATUS_CANCELLED
        assert live.cancelled_at is not None
        assert organization.plan == service.PLAN_WITHOUT_A_SUBSCRIPTION

    async def test_a_processor_that_refuses_is_not_reported_as_our_crash(self) -> None:
        """503, not 500. Every refusal used to reach the unhandled-exception
        handler: a currency the account cannot take answered "The request could
        not be completed", and the processor's actual words — which were in the
        log — never reached the person who could act on them."""
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor(refusal=BillingError("the processor refused the call: teapot"))
        session = CapturingSession(answers=[live])

        with pytest.raises(DependencyUnavailableError) as refused:
            await service.end_subscription(
                session,  # type: ignore[arg-type]
                configured=_configured(processor),
                organization=_organization(plan="pro"),
            )

        assert refused.value.problem.status == HTTPStatus.SERVICE_UNAVAILABLE

    async def test_the_processors_own_words_are_not_published(self) -> None:
        """`DomainError.detail` goes on the wire. A vendor's message is
        unbounded text we did not write and did not review."""
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor(refusal=BillingError("account 42 is suspended, ref XYZ"))
        session = CapturingSession(answers=[live])

        with pytest.raises(DependencyUnavailableError) as refused:
            await service.end_subscription(
                session,  # type: ignore[arg-type]
                configured=_configured(processor),
                organization=_organization(plan="pro"),
            )

        assert "account 42" not in refused.value.detail
        assert "XYZ" not in refused.value.detail
        # And it does not claim the arrangement ended, because it did not.
        assert "still active" in refused.value.detail

    async def test_the_plan_is_not_moved_here(self) -> None:
        """The processor decides when the arrangement ends, at its own period
        boundary. Dropping the tier the moment cancel was *asked for* would take
        away a month the customer has already paid for."""
        live = BillingSubscription(
            org_id=ORG_ID,
            processor=PROCESSOR_NAME,
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        organization = _organization(plan="pro")
        session = CapturingSession(answers=[live])

        await service.end_subscription(
            session,  # type: ignore[arg-type]
            configured=_configured(StubProcessor()),
            organization=organization,
        )

        assert organization.plan == "pro"

    async def test_an_organization_with_nothing_live_is_refused(self) -> None:
        session = CapturingSession(answers=[None])

        with pytest.raises(ConflictError, match="no live subscription"):
            await service.end_subscription(
                session,  # type: ignore[arg-type]
                configured=_configured(StubProcessor()),
                organization=_organization(),
            )

    async def test_an_arrangement_at_another_processor_is_not_cancelled_here(self) -> None:
        """ADR 0102 expects two. Naming this subscription at the wrong one would
        get a "not found" reported to the customer as a failed cancellation."""
        foreign = BillingSubscription(
            org_id=ORG_ID,
            processor="a-different-processor",
            subscription_ref=SUBSCRIPTION_REF,
            plan_slug="pro",
            status=service.STATUS_ACTIVE,
            services_committed=SERVICES_COMMITTED,
        )
        processor = StubProcessor()
        session = CapturingSession(answers=[foreign])

        with pytest.raises(ConflictError, match="not taken out through"):
            await service.end_subscription(
                session,  # type: ignore[arg-type]
                configured=_configured(processor),
                organization=_organization(plan="pro"),
            )
        assert processor.cancelled == []


# ------------------------------------------------ the billing screen (P1-75)


class _Rows:
    """One result, answering `.one()`, `.all()` or iteration with what a test supplied."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def one(self) -> Any:
        return self._row

    def all(self) -> list[Any]:
        return list(self._row)

    def __iter__(self) -> Any:
        return iter(self._row)


class OverviewSession:
    """Answers each read by the model it selects, so the assertions do not
    depend on the order the service happens to ask in.

    `CapturingSession` answers a queue, which is right for the webhook path —
    three reads in a fixed order. The screen makes six across two packages, and
    a queue there would fail on a harmless reordering rather than on a defect.
    """

    def __init__(
        self,
        *,
        plan: Plan | None,
        price: PlanPrice | None,
        subscription: BillingSubscription | None,
        services: int,
        ingested: tuple[int, int],
        frontier: tuple[datetime | None, int],
        services_committed: int | None = None,
    ) -> None:
        self.statements: list[Any] = []
        self.staged: list[Any] = []
        self._by_entity: dict[Any, Any] = {
            Plan: plan,
            PlanPrice: price,
            BillingSubscription: subscription,
            UsageDailyService: services,
        }
        # Several reads select a single column rather than an entity — the
        # committed count, the currency a mandate was signed in. Keyed by the
        # selected name, because the entity alone cannot tell them apart.
        self._by_column: dict[str, Any] = {
            "services_committed": services_committed,
            "currency": subscription.currency if subscription else None,
        }
        self._rows = [_Rows(ingested), _Rows(frontier)]

    async def scalars(self, statement: Any) -> Any:
        """Which currencies a tier is priced in. One, here: the suite states
        figures in a single currency and a second would prove nothing."""
        self.statements.append(statement)
        return _Rows([_PRO_PRICE.currency])

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        selected = statement.column_descriptions[0]
        if selected["name"] in self._by_column:
            return self._by_column[selected["name"]]
        return self._by_entity.get(selected["entity"])

    async def execute(self, statement: Any) -> _Rows:
        self.statements.append(statement)
        # `purchasable_plans` selects mapped *classes*; the aggregates queued by
        # the caller select `sum(...)` expressions, which name their parent
        # entity too — so the column count and the entity both fail to tell them
        # apart. A description whose `type` is its `entity` is the class itself.
        first = statement.column_descriptions[0]
        if first.get("type") is not None and first.get("type") is first.get("entity"):
            return _Rows([])
        return self._rows.pop(0)

    def add(self, instance: Any) -> None:
        self.staged.append(instance)

    @property
    def every_bound_value(self) -> set[Any]:
        return {
            value for statement in self.statements for value in statement.compile().params.values()
        }


def _overview_session(
    *,
    plan_slug: str = "free",
    price: PlanPrice | None = None,
    subscription: BillingSubscription | None = None,
    services: int = 2,
    ingested: tuple[int, int] = (1_000_000, 250_000),
    services_committed: int | None = None,
) -> OverviewSession:
    return OverviewSession(
        plan=_plan_row(plan_slug),
        price=_FREE_PRICE if price is None else price,
        subscription=subscription,
        services=services,
        ingested=ingested,
        frontier=(datetime(2026, 10, 1, tzinfo=UTC), 0),
        services_committed=services_committed,
    )


class TestTheBillingScreen:
    """P1-75. Which plan, what pays for it, and where the month stands."""

    async def test_the_allowance_figures_are_the_ones_usage_computes(self) -> None:
        """The whole point of the reuse. `allowance/allowance.go` warns that this
        rule is already written twice — once in Go for the door and once in
        `usage.billing` for the screen — and that a third copy means telling a
        customer a number nobody enforces. Every figure below is asserted
        against the function the usage screen calls for the same figure.
        """
        session = _overview_session(services=2)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        period = overview.period
        assert period.included_bytes == usage_billing.included_bytes(_FREE_PRICE, None)
        assert period.browser_included_bytes == usage_billing.browser_cap_bytes(_FREE_PRICE, None)
        assert period.billed_services == usage_billing.billed_services(2, _FREE_PRICE)
        assert period.service_cost_minor_units == usage_billing.service_cost_minor_units(
            2, _FREE_PRICE
        )
        assert period.allowance_enforced is usage_billing.allowance_is_enforced(_FREE_PRICE, None)

    async def test_the_screen_is_told_which_contact_fields_to_collect(self) -> None:
        """ADR 0109: the processor declares what it needs and the form renders
        exactly that. The stub asks for an email alone, deliberately unlike the
        domestic processor's three — a screen that hard-coded name, email and
        phone would pass against a double that mirrored one implementation."""
        session = _overview_session()

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.checkout is not None
        assert overview.checkout.required_contact_fields == ["email"]

    async def test_the_plan_and_the_month_are_readable_with_no_processor_configured(
        self,
    ) -> None:
        """Reading what you pay is not the same errand as paying. A free-tier
        customer must still see the allowance they are being stopped at, and a
        503 here would take the usage figures away over a gateway they never
        needed. Null checkout is what the screen reads as "upgrading is off"."""
        session = _overview_session()

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=None,
        )

        assert overview.checkout is None
        assert overview.period.allowance_enforced is True

    async def test_the_figures_are_quoted_in_the_currency_the_mandate_was_signed_in(
        self,
    ) -> None:
        """Not the caller's guess, and not a default. A customer who signed in
        rupees is quoted rupees, because that is the currency they know the
        product in and the one their bank will be debited in."""
        session = _overview_session(
            subscription=_pending(status=service.STATUS_ACTIVE, currency="INR")
        )

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.currency == "INR"

    async def test_a_caller_may_ask_for_a_currency(self) -> None:
        """The billing screen offers a switch, so somebody who has never paid can
        see rupees before deciding. It is an argument rather than something read
        off the request, because tax follows the payment instrument and never a
        network address."""
        session = _overview_session()

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
            currency="inr",
        )

        assert overview.currency == "INR"

    async def test_an_organization_that_has_never_paid_is_quoted_the_default(self) -> None:
        session = _overview_session(subscription=None)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.currency == usage_queries.DEFAULT_BILLING_CURRENCY

    async def test_the_period_is_the_calendar_month_the_day_falls_in(self) -> None:
        """ADR 0083: the bill covers a calendar month, not an anniversary."""
        session = _overview_session()

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.period.first_day == date(2026, 9, 1)
        assert overview.period.last_day == date(2026, 9, 30)

    async def test_a_free_tier_is_told_that_going_past_the_allowance_stops_ingestion(
        self,
    ) -> None:
        """ADR 0107's sentence, and the screen cannot write it without this flag.
        Sent rather than derived: which tiers are capped is policy, and a screen
        working it out from the price row is a second place for it to be wrong."""
        session = _overview_session(price=_FREE_PRICE)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.period.allowance_enforced is True

    async def test_a_paid_tier_with_no_mandate_is_not_told_that_it_will_be_stopped(self) -> None:
        """Nothing sizes the allowance, so there is no ceiling to warn about.

        This used to be the whole story for a paying customer — the docstring
        said `max_billed_services` is NULL on every paying tier "so nothing that
        pays is currently refused for volume". That was the G6a hole stated as a
        fact. It now describes only the case below it: no active mandate, so
        nothing to enforce, which is not a ceiling of zero.
        """
        session = _overview_session(plan_slug="pro", price=_PRO_PRICE)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(plan="pro"),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.period.allowance_enforced is False
        assert overview.period.included_bytes is None

    async def test_a_paid_tier_is_shown_the_ceiling_it_committed_to(self) -> None:
        """G6a on the screen. ADR 0107 promised the customer a ceiling they set
        and can see; showing it is the second half of enforcing it, because a
        customer stopped at a number they were never shown has been surprised
        into it — which is exactly what that ADR refuses."""
        session = _overview_session(plan_slug="pro", price=_PRO_PRICE, services_committed=5)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(plan="pro"),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        per_service = _PRO_PRICE.included_bytes_per_service
        assert per_service is not None, "the fixture must record an allowance to have one enforced"
        assert overview.period.allowance_enforced is True
        assert overview.period.included_bytes == 5 * per_service

    async def test_an_organization_that_has_never_paid_shows_no_subscription(self) -> None:
        """Null, not an invented row: a free-tier account has no arrangement, and
        a placeholder would read as one that failed."""
        session = _overview_session(subscription=None)

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.subscription is None

    async def test_the_subscription_is_shown_without_the_processors_name_or_reference(
        self,
    ) -> None:
        """ADR 0102 keeps the vendor out of everything above the adapter, and a
        subscription id is of use to nobody reading a screen."""
        session = _overview_session(
            plan_slug="pro",
            price=_PRO_PRICE,
            subscription=BillingSubscription(
                org_id=ORG_ID,
                processor=PROCESSOR_NAME,
                subscription_ref=SUBSCRIPTION_REF,
                plan_slug="pro",
                status=service.STATUS_ACTIVE,
                services_committed=SERVICES_COMMITTED,
                activated_at=datetime(2026, 9, 2, tzinfo=UTC),
            ),
        )

        overview = await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(plan="pro"),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert overview.subscription is not None
        assert overview.subscription.plan_slug == "pro"
        assert overview.subscription.status == service.STATUS_ACTIVE
        rendered = overview.model_dump_json()
        assert PROCESSOR_NAME not in rendered
        assert SUBSCRIPTION_REF not in rendered

    async def test_every_read_the_screen_makes_binds_this_organization(self) -> None:
        """The screen reads across two packages, and one query that lost its
        predicate would show another tenant's spend against this one's plan."""
        session = _overview_session(services=2)

        await service.overview(
            session,  # type: ignore[arg-type]
            organization=_organization(),
            today=TODAY,
            configured=_configured(StubProcessor()),
        )

        assert ORG_ID in session.every_bound_value
        assert OTHER_ORG_ID not in session.every_bound_value


class TestMoney:
    """An amount is a whole count of the smallest unit, and the currency travels
    with it — 600 is six dollars or six hundred yen depending on a field that
    must not live somewhere else."""

    def test_minor_units_become_major_units_without_floating_point(self) -> None:
        assert Money(minor_units=1800, currency="USD").major_units() == Decimal("18")

    def test_a_currency_whose_smallest_unit_is_unknown_is_refused(self) -> None:
        """The yen's minor unit is the yen. A default divisor of a hundred would
        bill 1% of a yen invoice and nothing would fail when it happened."""
        with pytest.raises(CurrencyUnknownError):
            Money(minor_units=600, currency="JPY").major_units()
