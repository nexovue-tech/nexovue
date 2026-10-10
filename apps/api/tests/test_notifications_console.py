"""The sender that spends nothing, and the guard that keeps development on it.

The defect this file covers was not in any mail code. Signing up sends a
verification message, so every account a test created spent one real message,
and three signups reach the development API per `just check`. The gate was a
recurring charge against a metered allowance, on changes that had nothing to do
with mail, and nothing said so until the provider reported 80% of a month's
quota gone.

So two things are proved here: that the console sender composes a real message
without sending it, and that the development stack is configured to use it. The
second matters more. The first is a class nobody has to remember; the second is
a line in a compose file that a merge could drop, and the failure it causes is
invisible until a bill arrives.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Final

import pytest
import structlog
from nexovue_platform.email import build_email_sender
from nexovue_platform.email.console import MESSAGE_ID_PREFIX, ConsoleEmailSender
from nexovue_platform.email.sender import EmailError, EmailSender, Message
from nexovue_platform.email.settings import CONSOLE_FROM_ADDRESS, EMAIL_VARIABLES, EmailSettings
from nexovue_platform.email.templates import TEMPLATES, render

import nexovue_api
from nexovue_api.settings import Settings

CONSOLE_SETTINGS: Final = EmailSettings(
    provider="console",
    api_key="",
    sending_domain="",
    from_address="noreply@mail.nexovue.com",
    reply_to="",
)

VERIFY_DATA: Final = {"link": "https://app.nexovue.com/verify?t=abc", "expires_in": "24 hours"}


@pytest.fixture(autouse=True)
def _without_ambient_mail_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """`just test-py` sources `.env`, which on a real machine holds real keys.

    Without this, what these assert would depend on whose machine ran them.
    """
    for name in EMAIL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _settings(**mail: str) -> Settings:
    """`Settings` with the stack filled in, so a test states only the mail part.

    Spelled out rather than splatted from a dict: `Settings` also takes
    pydantic-settings' own keyword-only parameters, and a `**dict[str, str]`
    cannot type-check against those.
    """
    return Settings(
        env="development",
        postgres_dsn="postgresql://nexovue:secret@localhost:5432/nexovue",
        clickhouse_dsn="clickhouse://nexovue:secret@localhost:8123/nexovue",
        **mail,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ the sender


async def test_the_rendered_body_reaches_the_log_so_a_link_can_be_followed() -> None:
    """The link is the only reason to log the body at all.

    A developer verifies a signup by copying the link out of the log. A sender
    that logged "would have sent VERIFY_EMAIL" would leave that flow with no way
    to complete by hand.
    """
    with structlog.testing.capture_logs() as events:
        delivery = await ConsoleEmailSender(CONSOLE_SETTINGS).send(
            to="person@example.com", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
        )

    (logged,) = events
    assert VERIFY_DATA["link"] in logged["mail_body"], (
        "the verification link must survive into the log"
    )
    assert logged["mail_to"] == "person@example.com"
    assert logged["mail_subject"] == TEMPLATES[Message.VERIFY_EMAIL][0]
    assert delivery.provider_message_id.startswith(MESSAGE_ID_PREFIX)


async def test_a_missing_template_field_refuses_exactly_as_the_real_provider_does() -> None:
    """The reason this renders rather than stubs.

    A development sender more permissive than the real one hides the defect it
    is most likely to be holding: a caller that stopped supplying a field. That
    would then surface in front of a customer.
    """
    with pytest.raises(EmailError) as refusal:
        await ConsoleEmailSender(CONSOLE_SETTINGS).send(
            to="person@example.com",
            message=Message.VERIFY_EMAIL,
            data={"link": "https://app.nexovue.com/verify?t=abc"},  # no expires_in
        )
    assert "expires_in" in str(refusal.value)


async def test_every_message_renders_so_none_is_only_exercised_in_production() -> None:
    """A template nobody sends in development is a template nobody reads."""
    data: dict[Message, dict[str, Any]] = {
        Message.VERIFY_EMAIL: VERIFY_DATA,
        Message.RESET_PASSWORD: VERIFY_DATA,
        Message.WELCOME_OWNER: {
            "organization": "Acme Inc",
            "link": "https://app.nexovue.com/invite?token=abc",
            "expires_in": "7 days",
        },
        Message.PAYMENT_FAILED: {
            "organization": "Acme Inc",
            "plan": "Pro",
            "until": "17 October 2026",
            "link": "https://app.nexovue.com/settings/billing",
        },
        Message.INVITE: {
            "inviter": "Raj",
            "organization": "Acme Inc",
            "role_phrase": "a member",
            "link": "https://app.nexovue.com/invite?token=abc",
            "expires_in": "7 days",
        },
        Message.INVOICE: {
            "period": "August 2026",
            "amount": "$49.00",
            "link": "https://app.nexovue.com/invoices/1",
        },
        Message.ALERT: {
            "subject": "[critical] Checkout API is down",
            "body": "Checkout API is down from 2 locations: status 503, expected < 400.",
            "link": "https://app.nexovue.com/alerts/incidents/1",
            "rule_name": "Any check down",
            "manage_link": "https://app.nexovue.com/alerts/rules/1",
        },
        Message.CHANNEL_FAILING: {
            "channel_name": "#incidents",
            "channel_kind": "Slack",
            "failing_for": "1 hour",
            "reason": "the receiver answered 404",
            "link": "https://app.nexovue.com/settings/channels/1",
        },
    }
    assert set(data) == set(TEMPLATES), "a message was added without a case here"

    for message, fields in data.items():
        delivery = await ConsoleEmailSender(CONSOLE_SETTINGS).send(
            to="person@example.com", message=message, data=fields
        )
        assert delivery.provider_message_id


def test_the_implementation_matches_the_port_signature() -> None:
    """Structural typing gives no error when a signature drifts apart."""
    assert inspect.signature(ConsoleEmailSender.send) == inspect.signature(EmailSender.send)


def test_naming_the_console_provider_is_all_it_takes_to_configure_mail() -> None:
    """No credential, because there is nothing for one to authenticate.

    A clone with no `.env` must still render mail — otherwise development has no
    verification link, and the cheapest way back is the real provider.
    """
    settings = _settings(email_provider="console")
    configured = settings.email

    assert configured is not None, "console must configure itself with no credentials"
    assert configured.from_address == CONSOLE_FROM_ADDRESS
    assert isinstance(build_email_sender(configured), ConsoleEmailSender)


def test_credentials_are_still_required_by_a_provider_that_authenticates() -> None:
    """The all-or-none rule is relaxed for console only, not removed."""
    partly = _settings(email_provider="resend", email_api_key="re_key")
    assert partly.email is None


# ------------------------------------------------- the guard on the dev stack


def _repository_root() -> Path:
    return Path(nexovue_api.__file__).parents[4]


def test_the_development_api_renders_mail_instead_of_sending_it() -> None:
    """The line that stops `just check` spending a real allowance.

    The suites reach the API over HTTP, so the container's environment decides
    whether a message leaves — not the test process's. Removing this from
    compose costs one real message per signup and reports nothing.
    """
    compose = _repository_root() / "deploy" / "compose" / "docker-compose.yaml"
    assert compose.is_file(), f"{compose} is the dev stack and must exist"

    assert "EMAIL_PROVIDER: console" in compose.read_text(encoding="utf-8"), (
        "the development API must be configured with the console mail provider. "
        "Without it every signup in every test sends a real message against a "
        "metered quota, and nothing in the gate reports that it happened."
    )


def test_a_subject_with_fields_is_filled_like_the_body() -> None:
    """Found by driving F21 end to end: the alert email's subject reached the log as
    the literal `{subject}`, because only bodies were ever filled — the first three
    messages all had fixed subjects, so nothing noticed."""
    subject, _ = render(
        Message.ALERT,
        {
            "subject": "[critical] Orders API is down",
            "body": "Orders API is down.",
            "link": "https://app.nexovue.test/synthetics/1",
            "rule_name": "Any check down",
            "manage_link": "https://app.nexovue.test/alerts/rules/1",
        },
    )
    assert subject == "[critical] Orders API is down"
