"""What each kind of channel needs, validated once for the API and the scheduler.

A channel's **configuration** is what may be shown — the addresses of an email
channel, a webhook's URL, PagerDuty's region. Its **secret** is what grants access
to the destination and is never shown again: the webhook URL of Slack, Teams and
Discord (the URL *is* the credential), a PagerDuty routing key, and the signing
secret nexovue generates for a generic webhook (ADR-010, amended).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from typing import Annotated, Final, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_EMAIL_ADDRESSES: Final = 20
MAX_URL_LENGTH: Final = 2048

# Standard Webhooks' format: a prefix, then the key in base64.
WEBHOOK_SECRET_PREFIX: Final = "whsec_"
WEBHOOK_SECRET_BYTES: Final = 32

# The kinds whose secret is the webhook URL itself.
URL_SECRET_KINDS: Final = frozenset({"slack", "teams", "discord"})
# The kinds a person reads, which grouping, the burst cap and quiet-hour digests
# shape (FR12, FR13, FR15). PagerDuty and webhooks are read by machines: each
# incident is its own event there, keyed for deduplication.
READ_BY_PEOPLE: Final = frozenset({"owners", "email", "slack", "teams", "discord"})
READ_BY_MACHINES: Final = frozenset({"pagerduty", "webhook"})

_EMAIL: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PAGERDUTY_KEY: Final = re.compile(r"^[A-Za-z0-9]{32}$")

PAGERDUTY_ENDPOINTS: Final = {
    "us": "https://events.pagerduty.com/v2/enqueue",
    "eu": "https://events.eu.pagerduty.com/v2/enqueue",
}


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmailConfig(_Config):
    addresses: Annotated[list[str], Field(min_length=1, max_length=MAX_EMAIL_ADDRESSES)]

    @field_validator("addresses")
    @classmethod
    def _addresses(cls, addresses: list[str]) -> list[str]:
        cleaned = [address.strip().lower() for address in addresses]
        for address in cleaned:
            if not _EMAIL.match(address) or len(address) > 320:
                raise ValueError(f"{address!r} is not an email address")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("an address is listed twice")
        return cleaned


class NoConfig(_Config):
    pass


class PagerDutyConfig(_Config):
    # PagerDuty runs two regions with separate endpoints; an account lives in one.
    region: Literal["us", "eu"] = "us"


class WebhookConfig(_Config):
    url: Annotated[str, Field(max_length=MAX_URL_LENGTH)]

    @field_validator("url")
    @classmethod
    def _url(cls, url: str) -> str:
        return require_url(url)


def config_model(kind: str) -> type[_Config]:
    """The configuration a kind takes. `owners` takes none: its recipients are
    whoever owns the organization when a message is sent."""
    match kind:
        case "email":
            return EmailConfig
        case "pagerduty":
            return PagerDutyConfig
        case "webhook":
            return WebhookConfig
        case _:
            return NoConfig


def require_url(url: str) -> str:
    """An `https` URL with a host. Plain `http` would carry an alert — and for
    Slack, Teams and Discord the credential itself — in the clear."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("must be an https:// URL")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"may be at most {MAX_URL_LENGTH} characters")
    return url


def validate_secret(kind: str, secret: str) -> str:
    """The secret a kind takes from the customer. Raises `ValueError` with the reason."""
    secret = secret.strip()
    if kind in URL_SECRET_KINDS:
        return require_url(secret)
    if kind == "pagerduty":
        if not _PAGERDUTY_KEY.match(secret):
            raise ValueError("a PagerDuty integration key is 32 letters and digits")
        return secret
    raise ValueError(f"a {kind} channel takes no secret")


def takes_secret(kind: str) -> bool:
    """Whether the customer supplies a secret. A webhook's is generated, not given."""
    return kind in URL_SECRET_KINDS or kind == "pagerduty"


def new_webhook_secret() -> str:
    return WEBHOOK_SECRET_PREFIX + base64.b64encode(os.urandom(WEBHOOK_SECRET_BYTES)).decode()


def webhook_signature(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """Standard Webhooks: `v1,` then the base64 HMAC-SHA256 of `id.timestamp.body`,
    keyed with the secret's decoded bytes."""
    key = base64.b64decode(secret.removeprefix(WEBHOOK_SECRET_PREFIX))
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def destination_host(kind: str, config: dict[str, object], secret: str | None) -> str | None:
    """Where a channel delivers, as far as may be shown: a host, never a path.

    A Slack or Discord URL's path is its credential, so only the host of any URL
    is ever displayed or logged.
    """
    if kind == "webhook":
        return urlsplit(str(config.get("url", ""))).hostname
    if kind in URL_SECRET_KINDS and secret:
        return urlsplit(secret).hostname
    if kind == "pagerduty":
        region = str(config.get("region", "us"))
        return urlsplit(PAGERDUTY_ENDPOINTS.get(region, PAGERDUTY_ENDPOINTS["us"])).hostname
    return None
