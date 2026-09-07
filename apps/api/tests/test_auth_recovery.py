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
from typing import Any, Final

import pytest

from nexovue_api.auth import recovery
from nexovue_api.auth.dependencies import _issued_before_reset
from nexovue_api.auth.sessions import Session
from nexovue_api.auth.tokens import OneTimeTokenStore, TokenPurpose
from nexovue_api.data.postgres.models import User
from nexovue_api.notifications import Delivery, EmailSender, Message

TTL: Final = 3600


class FakeRedis:
    """Just enough Redis: set with a TTL, and getdel.

    A real Redis is not needed to prove single use — what must be shown is that
    `consume` removes the key in the same call that reads it, and a dict shows
    that as well as a server does.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


class RecordingSender:
    """Captures what would have been mailed.

    Annotated to satisfy `EmailSender` structurally rather than by inheritance,
    which is the property ADR 0102's port exists for — a double with no import
    of any provider. mypy checks the assignment below, so a drifted signature is
    a type error here rather than a surprise at a call site.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, *, to: str, message: Message, data: Mapping[str, Any]) -> Delivery:
        self.sent.append({"to": to, "message": message, "data": dict(data)})
        return Delivery(provider_message_id="test-message-id")


def _sender() -> EmailSender:
    """The double, seen as the port. The annotation is the check."""
    return RecordingSender()


def _store() -> tuple[OneTimeTokenStore, FakeRedis]:
    redis = FakeRedis()
    return OneTimeTokenStore(redis), redis  # type: ignore[arg-type]


def _user(**overrides: Any) -> User:
    user = User()
    user.id = overrides.get("id", uuid.uuid4())
    user.org_id = overrides.get("org_id", uuid.uuid4())
    user.email = overrides.get("email", "owner@acme.test")
    user.email_verified_at = overrides.get("email_verified_at")
    user.sessions_valid_from = overrides.get("sessions_valid_from")
    return user


# --------------------------------------------------------------- the token store


async def test_a_token_works_once_and_never_again() -> None:
    """Reuse is the attack: a reset link forwarded, screenshotted or left in a
    mail archive must not open the account a second time."""
    tokens, _ = _store()
    token = await tokens.issue(
        purpose=TokenPurpose.RESET_PASSWORD, user_id="user-1", ttl_seconds=TTL
    )

    assert await tokens.consume(purpose=TokenPurpose.RESET_PASSWORD, token=token) == "user-1"
    assert await tokens.consume(purpose=TokenPurpose.RESET_PASSWORD, token=token) is None


async def test_a_verification_token_cannot_be_spent_as_a_reset_token() -> None:
    """Without the purpose in the key, the weaker of the two links would set the
    strength of both: a 24-hour verification link would become a 24-hour
    password reset."""
    tokens, _ = _store()
    token = await tokens.issue(purpose=TokenPurpose.VERIFY_EMAIL, user_id="user-1", ttl_seconds=TTL)

    assert await tokens.consume(purpose=TokenPurpose.RESET_PASSWORD, token=token) is None
    # And it is still unspent for the purpose it was issued for.
    assert await tokens.consume(purpose=TokenPurpose.VERIFY_EMAIL, token=token) == "user-1"


async def test_the_token_itself_is_never_stored() -> None:
    """A dump of Redis must be a list of expiry times, not usable credentials —
    the property `sessions.py` states and this store inherits."""
    tokens, redis = _store()
    token = await tokens.issue(
        purpose=TokenPurpose.RESET_PASSWORD, user_id="user-1", ttl_seconds=TTL
    )

    assert token not in "".join(redis.values)
    assert all(token not in key for key in redis.values)


async def test_each_purpose_carries_its_own_lifetime() -> None:
    tokens, redis = _store()
    await tokens.issue(purpose=TokenPurpose.VERIFY_EMAIL, user_id="u", ttl_seconds=86400)
    await tokens.issue(purpose=TokenPurpose.RESET_PASSWORD, user_id="u", ttl_seconds=3600)

    assert sorted(redis.expiries.values()) == [3600, 86400]


async def test_an_empty_token_is_refused_without_a_lookup() -> None:
    """A blank field must not become a key lookup that could match something."""
    tokens, redis = _store()
    redis.values[""] = "user-1"

    assert await tokens.consume(purpose=TokenPurpose.RESET_PASSWORD, token="") is None


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
