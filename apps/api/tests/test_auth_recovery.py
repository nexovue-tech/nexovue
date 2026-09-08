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
from nexovue_api.auth.tokens import EmailChange, OneTimeTokenStore, TokenPurpose
from nexovue_api.data.postgres.models import User
from nexovue_api.notifications import Delivery, EmailError, EmailSender, Message

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


# ------------------------------------------------- the change-of-address claim


async def test_a_change_link_carries_the_address_it_was_minted_for() -> None:
    """**The reason the address is in the token and not on the row.**

    A `users.pending_email` column would be overwritten by a second request, and
    following the first link would then apply an address that link never proved
    — control of one inbox standing in as proof of another.
    """
    tokens, _ = _store()
    first = await tokens.issue_email_change(
        user_id="user-1", new_email="first@acme.test", ttl_seconds=TTL
    )
    second = await tokens.issue_email_change(
        user_id="user-1", new_email="second@acme.test", ttl_seconds=TTL
    )

    assert (await tokens.consume_email_change(token=first)) == EmailChange(
        user_id="user-1", new_email="first@acme.test"
    )
    assert (await tokens.consume_email_change(token=second)) == EmailChange(
        user_id="user-1", new_email="second@acme.test"
    )


async def test_a_change_link_works_once_and_never_again() -> None:
    tokens, _ = _store()
    token = await tokens.issue_email_change(
        user_id="user-1", new_email="new@acme.test", ttl_seconds=TTL
    )

    assert await tokens.consume_email_change(token=token) is not None
    assert await tokens.consume_email_change(token=token) is None


@pytest.mark.parametrize("purpose", [TokenPurpose.VERIFY_EMAIL, TokenPurpose.RESET_PASSWORD])
async def test_a_change_link_cannot_be_spent_as_another_kind(purpose: TokenPurpose) -> None:
    """A change link lives as long as a verification link. Without the purpose
    in the key it would be a 24-hour password reset."""
    tokens, _ = _store()
    token = await tokens.issue_email_change(
        user_id="user-1", new_email="new@acme.test", ttl_seconds=TTL
    )

    assert await tokens.consume(purpose=purpose, token=token) is None
    assert await tokens.consume_email_change(token=token) is not None


@pytest.mark.parametrize("purpose", [TokenPurpose.VERIFY_EMAIL, TokenPurpose.RESET_PASSWORD])
async def test_another_kind_of_token_cannot_be_spent_as_a_change(purpose: TokenPurpose) -> None:
    """The other direction, which is the dangerous one: a verification link that
    could move an address would let a signup mail rewrite an account."""
    tokens, _ = _store()
    token = await tokens.issue(purpose=purpose, user_id="user-1", ttl_seconds=TTL)

    assert await tokens.consume_email_change(token=token) is None
    assert await tokens.consume(purpose=purpose, token=token) == "user-1"


@pytest.mark.parametrize("method", ["issue", "consume"])
async def test_a_change_purpose_cannot_be_reached_through_the_id_valued_methods(
    method: str,
) -> None:
    """A programming error, refused where it is made. `consume` would otherwise
    hand back a JSON document where its caller expected a user id."""
    tokens, _ = _store()

    with pytest.raises(ValueError, match="more than a user id"):
        if method == "issue":
            await tokens.issue(purpose=TokenPurpose.CHANGE_EMAIL, user_id="u", ttl_seconds=TTL)
        else:
            await tokens.consume(purpose=TokenPurpose.CHANGE_EMAIL, token="t")


@pytest.mark.parametrize("stored", ["not json at all", '"a string"', "{}", '{"user_id": 7}'])
async def test_a_claim_that_will_not_decode_is_refused_rather_than_raising(stored: str) -> None:
    """The only way to hold one of these is to have written it into Redis by
    hand. A caller that crashed on it would be a caller that can be made to
    crash from outside the application."""
    tokens, redis = _store()
    token = await tokens.issue_email_change(
        user_id="user-1", new_email="new@acme.test", ttl_seconds=TTL
    )
    redis.values[next(iter(redis.values))] = stored

    assert await tokens.consume_email_change(token=token) is None


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

    async def send(self, *, to: str, message: Message, data: Mapping[str, Any]) -> Delivery:
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
