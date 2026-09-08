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

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT

from nexovue_api.billing import (
    BillingEvent,
    BillingEventKind,
    ConfiguredProcessor,
    SignatureInvalidError,
    UnhandledBillingEventError,
    queries,
    service,
)
from nexovue_api.billing.cashfree import (
    DELIVERY_HEADER,
    ORG_TAG,
    PLAN_TAG,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    CashfreePaymentProcessor,
)
from nexovue_api.billing.schemas import DeliveryOutcome
from nexovue_api.data.postgres.models import BillingSubscription, BillingWebhookDelivery
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.settings import (
    API_PREFIX,
    DOMESTIC_PAYMENT_VARIABLES,
    DomesticPaymentSettings,
    Settings,
)

ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-000000000001")
OTHER_ORG_ID: Final = uuid.UUID("0198f2a0-0000-7000-8000-0000000000ff")
SUBSCRIPTION_REF: Final = "nexovue-sub-0198f2a0"
DELIVERY_ID: Final = "6f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"
PROCESSOR_NAME: Final = "cashfree"

SECRET: Final = "test_secret_key_never_a_real_one"
TIMESTAMP: Final = "1757318400000"
SETTINGS: Final = DomesticPaymentSettings(
    provider="cashfree",
    app_id="TEST_APP_ID",
    secret_key=SECRET,
    environment="sandbox",
    base_url="https://sandbox.example.test/pg",
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
    seen: list[bytes] | None = None

    async def begin_checkout(self, *, org_id: uuid.UUID, plan_slug: str, billing_email: str) -> Any:
        raise NotImplementedError

    async def cancel(self, *, subscription_ref: str) -> None:
        raise NotImplementedError

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
    org_id: uuid.UUID = ORG_ID,
) -> BillingEvent:
    return BillingEvent(
        kind=kind,
        org_id=org_id,
        subscription_ref=SUBSCRIPTION_REF,
        plan_slug="pro",
        delivery_id=DELIVERY_ID,
    )


def _configured(processor: Any) -> ConfiguredProcessor:
    return ConfiguredProcessor(name=PROCESSOR_NAME, processor=processor)


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


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
    already_seen = CapturingSession(answers=[uuid.uuid4()])
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


async def test_a_first_activation_creates_the_arrangement_and_records_the_delivery(
    session: CapturingSession,
) -> None:
    configured = _configured(StubProcessor(event=_event()))

    acknowledgement = await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=configured,
        body=b"{}",
        headers={},
    )

    assert acknowledgement.outcome is DeliveryOutcome.RECORDED
    subscription = session.staged_of(BillingSubscription)[0]
    assert subscription.org_id == ORG_ID
    assert subscription.processor == PROCESSOR_NAME
    assert subscription.subscription_ref == SUBSCRIPTION_REF
    assert subscription.plan_slug == "pro"
    assert subscription.status == service.STATUS_ACTIVE
    assert subscription.activated_at is not None

    delivery = session.staged_of(BillingWebhookDelivery)[0]
    assert delivery.org_id == ORG_ID
    assert delivery.delivery_id == DELIVERY_ID
    assert delivery.kind == BillingEventKind.SUBSCRIPTION_ACTIVATED.value


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
    )
    session = CapturingSession(answers=[None, live])
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
    )
    session = CapturingSession(answers=[None, live])

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
        activated_at=first_activation,
    )
    session = CapturingSession(answers=[None, live])

    await service.record_from_webhook(
        session,  # type: ignore[arg-type]
        configured=_configured(StubProcessor(event=_event())),
        body=b"{}",
        headers={},
    )

    assert live.activated_at == first_activation


# --------------------------------------------------------- signature to stored row


def _signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), TIMESTAMP.encode() + body, hashlib.sha256)
    return {
        TIMESTAMP_HEADER: TIMESTAMP,
        SIGNATURE_HEADER: base64.b64encode(digest.digest()).decode("ascii"),
        DELIVERY_HEADER: DELIVERY_ID,
    }


def _cashfree_body() -> bytes:
    return json.dumps(
        {
            "data": {
                "subscription_details": {
                    "subscription_id": SUBSCRIPTION_REF,
                    "subscription_status": "ACTIVE",
                    "subscription_tags": {ORG_TAG: str(ORG_ID), PLAN_TAG: "pro"},
                }
            },
            "type": "SUBSCRIPTION_STATUS_CHANGED",
        }
    ).encode()


async def test_a_real_signed_delivery_reaches_a_row_and_a_tampered_one_does_not() -> None:
    """End to end through the real processor: bytes in, a row staged — and the
    same bytes with one character changed reach nothing at all."""
    body = _cashfree_body()
    headers = _signed(body)
    configured = _configured(CashfreePaymentProcessor(SETTINGS))

    accepted = CapturingSession()
    outcome = await service.record_from_webhook(
        accepted,  # type: ignore[arg-type]
        configured=configured,
        body=body,
        headers=headers,
    )
    assert outcome.outcome is DeliveryOutcome.RECORDED
    assert accepted.staged_of(BillingSubscription)[0].org_id == ORG_ID

    tampered_body = body.replace(b'"pro"', b'"fre"')
    refused = CapturingSession()
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
        CashfreePaymentProcessor(SETTINGS)
    )
    return app


async def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_the_door_accepts_a_signed_delivery_at_the_role_named_path() -> None:
    """A URL pasted into somebody else's dashboard. Naming the role rather than
    the vendor is what lets the second processor have its own."""
    body = _cashfree_body()
    session = CapturingSession()

    async with await _client(_build_app(session)) as client:
        response = await client.post(
            f"{API_PREFIX}/billing/webhook/domestic", content=body, headers=_signed(body)
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == DeliveryOutcome.RECORDED.value
    assert session.staged_of(BillingSubscription)[0].org_id == ORG_ID


async def test_the_door_refuses_a_tampered_delivery_without_writing() -> None:
    """The body is read as raw bytes: a pydantic body parameter would have
    FastAPI answer 422 on a forgery before the signature was ever looked at."""
    body = _cashfree_body()
    headers = _signed(body)
    session = CapturingSession()

    async with await _client(_build_app(session)) as client:
        response = await client.post(
            f"{API_PREFIX}/billing/webhook/domestic",
            content=body.replace(b'"pro"', b'"fre"'),
            headers=headers,
        )

    assert response.status_code == 401
    assert session.staged == []
