"""Sending an alert through the mail port: each outcome the outbox must tell apart."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexovue_platform.email import Delivery, EmailError, Message

from nexovue_scheduler.alerts.messages import AlertMessage
from nexovue_scheduler.alerts.senders import email

MESSAGE = AlertMessage(
    kind="opened",
    severity="critical",
    summary="[critical] Checkout API is down",
    details=("Checkout API is down.", "Rule: Any check down."),
    link="https://app.nexovue.test/synthetics/1",
    rule_name="Any check down",
    manage_link=None,
)


class _Recorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, Message, Mapping[str, Any]]] = []
        self.keys: list[str | None] = []
        self._fail = fail

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        self.keys.append(idempotency_key)
        if self._fail:
            raise EmailError("could not reach the mail provider: timed out")
        self.sent.append((to, message, data))
        return Delivery(provider_message_id="test")


async def test_each_address_receives_the_alert() -> None:
    sender = _Recorder()
    result = await email.send(
        sender,
        ["a@example.test", "b@example.test"],
        MESSAGE,
        message_id="batch-1",
        fallback_manage_link="https://app.nexovue.test/alerts",
        owners=False,
    )
    assert result.outcome == "delivered"
    assert [to for to, _, _ in sender.sent] == ["a@example.test", "b@example.test"]
    _, message, data = sender.sent[0]
    assert message is Message.ALERT
    assert data["subject"] == MESSAGE.summary
    assert data["manage_link"] == "https://app.nexovue.test/alerts"


async def test_an_unreachable_provider_is_retried() -> None:
    result = await email.send(
        _Recorder(fail=True),
        ["a@example.test"],
        MESSAGE,
        message_id="batch-1",
        fallback_manage_link="x",
        owners=False,
    )
    assert result.outcome == "retry"
    assert result.reason == "could not reach the mail provider: timed out"


async def test_mail_that_is_not_configured_says_so_and_is_final() -> None:
    result = await email.send(
        None,
        ["a@example.test"],
        MESSAGE,
        message_id="batch-1",
        fallback_manage_link="x",
        owners=False,
    )
    assert result.outcome == "failed"
    assert result.reason == email.NOT_CONFIGURED


async def test_owners_with_no_verified_address_say_so() -> None:
    result = await email.send(
        _Recorder(), [], MESSAGE, message_id="batch-1", fallback_manage_link="x", owners=True
    )
    assert result.outcome == "failed"
    assert result.reason == email.NO_VERIFIED_OWNER


async def test_a_retry_carries_the_same_key_per_address_so_nobody_is_mailed_twice() -> None:
    """One address refused, the batch retried: the address that already had it is
    sent again under the key it had, which the provider drops."""

    async def attempt(message_id: str) -> list[str | None]:
        sender = _Recorder()
        await email.send(
            sender,
            ["a@example.test", "b@example.test"],
            MESSAGE,
            message_id=message_id,
            fallback_manage_link="x",
            owners=False,
        )
        return sender.keys

    first, retry, other_batch = (
        await attempt("batch-1"),
        await attempt("batch-1"),
        await attempt("batch-2"),
    )
    assert first == retry
    assert len(set(first)) == 2
    assert not set(first) & set(other_batch)
    assert all(key is not None and len(key) <= 256 for key in first)
