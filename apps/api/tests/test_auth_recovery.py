"""Verification and password reset, driven without a network (P1-70, P1-71).

The security properties here are all *absences* — no oracle, no reuse, no
surviving session — and an absence is exactly what a test written after the fact
tends to assert loosely. Each one below names the attack it prevents, so a future
reader can tell whether a change weakened it.

The live suite is separate: `test_auth_recovery_live.py` sends a real message.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import pytest
from fake_stores import FakeOneTimeTokenStore
from nexovue_platform.email import Delivery, EmailError, EmailSender, Message
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth import recovery
from nexovue_api.auth.dependencies import _issued_before_reset
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.tokens import OneTimeTokenStore, TokenPurpose
from nexovue_api.data.postgres.models import User

TTL: Final = 3600


class RecordingSender:
    """Captures what would have been mailed.

    Annotated to satisfy `EmailSender` structurally rather than by inheritance,
    which is the property the port exists for — a double with no import of any
    provider. mypy checks the assignment below, so a drifted signature is a type
    error here rather than a surprise at a call site.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.sent.append({"to": to, "message": message, "data": dict(data)})
        return Delivery(provider_message_id="test-message-id")


def _sender() -> EmailSender:
    """The double, seen as the port. The annotation is the check."""
    return RecordingSender()


def _store() -> tuple[OneTimeTokenStore, FakeOneTimeTokenStore]:
    fake = FakeOneTimeTokenStore()
    return cast(OneTimeTokenStore, fake), fake


def _user(**overrides: Any) -> User:
    user = User()
    user.id = overrides.get("id", uuid.uuid4())
    user.org_id = overrides.get("org_id", uuid.uuid4())
    user.email = overrides.get("email", "owner@acme.test")
    user.email_verified_at = overrides.get("email_verified_at")
    user.sessions_valid_from = overrides.get("sessions_valid_from")
    return user


# The token store's own guarantees — single use, purpose, nothing stored in the
# clear, expiry — are proved against Postgres in `test_auth_recovery_live.py`.


async def test_a_change_purpose_cannot_be_reached_through_the_id_valued_methods() -> None:
    """A programming error, refused where it is made, before any statement runs."""
    tokens = OneTimeTokenStore(cast(AsyncSession, None))

    with pytest.raises(ValueError, match="more than a user id"):
        await tokens.issue(purpose=TokenPurpose.CHANGE_EMAIL, user_id="u", ttl_seconds=TTL)
    with pytest.raises(ValueError, match="more than a user id"):
        await tokens.consume(purpose=TokenPurpose.CHANGE_EMAIL, token="t")


# ------------------------------------------------------------------ the flows


async def test_a_reset_link_points_at_the_app_and_carries_the_token() -> None:
    """The customer clicks this in a mail client, so it must reach a screen —
    not this API, which would answer JSON to a human."""
    tokens, _ = _store()
    sender = RecordingSender()
    user = _user()

    await recovery.send_verification(
        tokens,
        sender,
        user=user,
        app_base_url="https://app.nexovue.com/",
        ttl_seconds=86400,
    )

    link = sender.sent[0]["data"]["link"]
    assert link.startswith("https://app.nexovue.com/verify-email?token=")
    # The trailing slash on the base must not survive into a double slash.
    assert "//verify-email" not in link
    assert sender.sent[0]["to"] == "owner@acme.test"


async def test_a_verified_address_is_sent_nothing_when_resend_is_pressed() -> None:
    tokens, _ = _store()
    sender = RecordingSender()

    await recovery.send_verification(
        tokens,
        sender,
        user=_user(email_verified_at=datetime.now(UTC)),
        app_base_url="https://app.nexovue.com",
        ttl_seconds=86400,
    )

    assert sender.sent == []


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(3600, "1 hour"), (86400, "24 hours"), (7200, "2 hours"), (60, "1 hour")],
)
def test_the_mail_describes_the_lifetime_the_token_actually_has(
    seconds: int, expected: str
) -> None:
    """Rendered from the TTL rather than written twice, so changing one cannot
    leave the message claiming the other."""
    assert recovery.describe_lifetime(seconds) == expected


# ------------------------------------------------------- the session epoch


def test_a_session_older_than_the_reset_is_refused() -> None:
    """The half of P1-71 that is easy to leave out: an attacker holding a live
    session does not need the password the customer just changed."""
    reset_at = datetime.now(UTC)
    older = Session(user_id="u", org_id="o", created_at=reset_at - timedelta(seconds=1))

    assert _issued_before_reset(older, _user(sessions_valid_from=reset_at))


def test_a_session_created_after_the_reset_stands() -> None:
    reset_at = datetime.now(UTC)
    newer = Session(user_id="u", org_id="o", created_at=reset_at + timedelta(seconds=1))

    assert not _issued_before_reset(newer, _user(sessions_valid_from=reset_at))


def test_every_session_stands_when_no_reset_has_happened() -> None:
    """NULL is the state of every user until one resets, so a wrong reading here
    would sign out the entire product."""
    ancient = Session(user_id="u", org_id="o", created_at=datetime(2020, 1, 1, tzinfo=UTC))

    assert not _issued_before_reset(ancient, _user(sessions_valid_from=None))


class RefusingSender:
    """A provider that is having a bad minute. Satisfies `EmailSender`."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.attempts += 1
        raise EmailError("the mail provider refused the message (500): upstream unavailable")


async def test_a_failing_provider_does_not_reveal_that_the_address_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oracle this endpoint is shaped to avoid, and it was open until 2026-09-08.

    A send that raised escaped as a 5xx for a known address while an unknown one
    still got 202 — so a provider having a bad minute told a stranger which
    addresses have accounts, exactly when nobody would think to look.

    Both calls must return without raising. The known one must also have *tried*,
    or this would pass by never sending at all.
    """
    known = _user(email="owner@acme.test")
    tokens, _ = _store()
    sender = RefusingSender()

    async def finds_the_user(_db: object, _email: str) -> tuple[User, object] | None:
        return (known, object())

    async def finds_nobody(_db: object, _email: str) -> tuple[User, object] | None:
        return None

    monkeypatch.setattr(recovery, "find_active_user_by_email", finds_the_user)
    await recovery.request_password_reset(
        None,  # type: ignore[arg-type]
        tokens,
        sender,
        address="owner@acme.test",
        app_base_url="https://app.nexovue.com",
        ttl_seconds=TTL,
    )
    assert sender.attempts == 1, "the known address was never actually attempted"

    monkeypatch.setattr(recovery, "find_active_user_by_email", finds_nobody)
    await recovery.request_password_reset(
        None,  # type: ignore[arg-type]
        tokens,
        sender,
        address="nobody@acme.test",
        app_base_url="https://app.nexovue.com",
        ttl_seconds=TTL,
    )
    assert sender.attempts == 1, "an unknown address must not reach the provider"
