"""Channel configuration, secrets and signing, shared by the API and the scheduler."""

from __future__ import annotations

import base64
import os

import pytest
from nexovue_platform.alerts.channels import (
    EmailConfig,
    WebhookConfig,
    destination_host,
    new_webhook_secret,
    takes_secret,
    validate_secret,
    webhook_signature,
)
from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    NOTIFICATION_CHANNEL_PURPOSE,
    SealedBox,
    SealError,
    channel_secret_owner,
)
from pydantic import ValidationError

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXXXXXXXXX"


def test_the_standard_webhooks_signature_matches_the_published_vector() -> None:
    """The example in the Standard Webhooks specification, so a receiver using any
    of its libraries verifies what we send (FR4, AC3)."""
    signature = webhook_signature(
        "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        "msg_p5jXN8AQM9LWM0D4loKWxJek",
        1614265330,
        b'{"test": 2432232314}',
    )
    assert signature == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_a_generated_webhook_secret_is_32_random_bytes_in_the_standard_format() -> None:
    secret = new_webhook_secret()
    assert secret.startswith("whsec_")
    assert len(base64.b64decode(secret.removeprefix("whsec_"))) == 32
    assert new_webhook_secret() != secret


def test_a_channel_secret_opens_only_for_its_own_channel() -> None:
    key = os.urandom(32)
    box = SealedBox(key, purpose=NOTIFICATION_CHANNEL_PURPOSE)
    sealed = box.seal(SLACK_URL, owner=channel_secret_owner("org-1", "channel-1"))

    assert box.open(sealed, owner=channel_secret_owner("org-1", "channel-1")) == SLACK_URL
    with pytest.raises(SealError):
        box.open(sealed, owner=channel_secret_owner("org-1", "channel-2"))
    with pytest.raises(SealError):
        SealedBox(key, purpose=CHECK_SECRET_PURPOSE).open(
            sealed, owner=channel_secret_owner("org-1", "channel-1")
        )


def test_an_incoming_webhook_url_must_be_https() -> None:
    assert validate_secret("slack", f" {SLACK_URL} ") == SLACK_URL
    with pytest.raises(ValueError, match="https"):
        validate_secret("discord", "http://discord.com/api/webhooks/1/abc")


def test_a_pagerduty_key_is_32_letters_and_digits() -> None:
    assert validate_secret("pagerduty", "a" * 32) == "a" * 32
    with pytest.raises(ValueError, match="32"):
        validate_secret("pagerduty", "short")


def test_only_the_kinds_the_customer_supplies_a_secret_for_take_one() -> None:
    assert {
        kind
        for kind in ("owners", "email", "slack", "teams", "discord", "pagerduty", "webhook")
        if takes_secret(kind)
    } == {"slack", "teams", "discord", "pagerduty"}
    with pytest.raises(ValueError):
        validate_secret("webhook", "anything")


def test_email_addresses_are_checked_and_not_repeated() -> None:
    assert EmailConfig(addresses=[" On-Call@Example.test "]).addresses == ["on-call@example.test"]
    with pytest.raises(ValidationError):
        EmailConfig(addresses=["not an address"])
    with pytest.raises(ValidationError):
        EmailConfig(addresses=["a@example.test", "A@example.test"])
    with pytest.raises(ValidationError):
        EmailConfig(addresses=[])


def test_a_webhook_url_must_be_https() -> None:
    assert (
        WebhookConfig(url="https://hooks.example.test/nexovue").url
        == "https://hooks.example.test/nexovue"
    )
    with pytest.raises(ValidationError):
        WebhookConfig(url="http://hooks.example.test/nexovue")


def test_only_the_host_of_a_destination_is_ever_shown() -> None:
    """A Slack or Discord URL's path is its credential."""
    assert destination_host("slack", {}, SLACK_URL) == "hooks.slack.com"
    assert (
        destination_host("webhook", {"url": "https://hooks.example.test/a/b?c=d"}, None)
        == "hooks.example.test"
    )
    assert destination_host("pagerduty", {"region": "eu"}, "k" * 32) == "events.eu.pagerduty.com"
    assert destination_host("email", {"addresses": ["a@b.c"]}, None) is None
