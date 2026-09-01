"""What the key endpoints put on the wire.

Both kinds carry a `kind` on every response. It is a `Literal`, fixed per class
rather than a field somebody sets, so a secret key cannot be serialized as public
or the reverse: the label travels with the value, the way `SecretStr` makes the
redaction travel with the secret. ADR 0086.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_serializer, field_validator

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH

# The console shows this beside the secret. It is the reason the storage is safe,
# not an apology: a key we could show twice is a key we would have to store.
SHOWN_ONCE_NOTICE: Final = (
    "This is the only time this key is shown. We store a SHA-256 hash of it and "
    "never the key itself, so nobody — including us — can read it back. Copy it "
    "into your exporter now; if you lose it, revoke this key and create another."
)

SECRET_KIND: Final = "secret"
PUBLIC_KIND: Final = "public"

# An origin is scheme + host + optional port and nothing else (RFC 6454 §6.2).
# Anything a customer might reasonably type that is not one of those is refused
# here rather than accepted and then silently never matched by the browser door.
#
# These rules mirror `apps/receiver/internal/otlp/origin.go`, which does the
# matching. Accepting an entry the receiver will never match would be worse than
# refusing it: the customer would see a configured origin and a refused request
# and have nothing connecting the two.
ORIGIN_SCHEMES: Final = ("http", "https")
DEFAULT_PORTS: Final = {"http": 80, "https": 443}

# "https://*.example.com" covers its subdomains. Two labels minimum, so
# "https://*.com" cannot be written — the same floor the receiver enforces.
WILDCARD_PREFIX: Final = "*."
MIN_WILDCARD_LABELS: Final = 2

ORIGIN_FORMAT_DETAIL: Final = (
    "Each allowed origin must be scheme://host with an optional port, and nothing "
    "else — for example https://app.example.com or http://localhost:3000. No path, "
    "query, fragment, credentials or trailing slash. A leading *. covers subdomains "
    "of a name with at least two labels, as in https://*.example.com."
)
NO_ORIGINS_DETAIL: Final = (
    "A site key needs at least one allowed origin. An empty list would accept "
    "nothing, because origin is the only thing restricting a public key."
)
MAX_ALLOWED_ORIGINS: Final = 20


def _check_host(host: str) -> None:
    """A host, or a wildcard over one. Refuses the wildcard that means everything."""
    name = host
    if name.startswith(WILDCARD_PREFIX):
        name = name[len(WILDCARD_PREFIX) :]
        if len(name.split(".")) < MIN_WILDCARD_LABELS:
            raise ValueError(ORIGIN_FORMAT_DETAIL)
    if not name or "*" in name or name.startswith(".") or name.endswith("."):
        raise ValueError(ORIGIN_FORMAT_DETAIL)


def normalize_origin(candidate: str) -> str:
    """One origin, lower-cased and proved to be an origin rather than a URL.

    Scheme and host are ASCII-lower-cased because a browser sends them that way,
    and a scheme's default port is dropped, because `https://x` and `https://x:443`
    are the one origin. A stored `https://App.Example.com:443` that did not go
    through this would never match what a browser actually sends.
    """
    trimmed = candidate.strip().rstrip("/")
    parts = urlsplit(trimmed)
    scheme = parts.scheme.lower()
    disqualified = (
        scheme not in ORIGIN_SCHEMES
        or not parts.netloc
        or "@" in parts.netloc
        or parts.path
        or parts.query
        or parts.fragment
    )
    if disqualified:
        raise ValueError(ORIGIN_FORMAT_DETAIL)
    try:
        port = parts.port
    except ValueError as invalid_port:  # urlsplit defers the parse until asked
        raise ValueError(ORIGIN_FORMAT_DETAIL) from invalid_port

    host = (parts.hostname or "").lower()
    _check_host(host)

    if port is None or port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


class ApiKeySummary(BaseModel):
    """A secret key as the console shows it.

    The secret was shown once at creation and is not stored. `key_hint` is the
    scheme plus the last four characters of the prefix — enough for a customer
    holding a leaked key to tell which row to revoke, and far short of the
    twenty-two random characters the secret is made of.
    """

    kind: Literal["secret"] = SECRET_KIND
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    key_hint: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeySummary]


class ApiKeyCreateRequest(BaseModel):
    """Which project the key is for, and what to call it.

    The name is what a customer reads when deciding which key to withdraw, so it
    is required rather than defaulted: five keys called "default" are five keys
    nobody can tell apart under the pressure of a leak.
    """

    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)


class ApiKeyCreated(BaseModel):
    """A new key, with its secret — the one and only time it exists on the wire.

    `key` is the plaintext. It is not stored, not logged and not recoverable, so
    this response body is the whole of its existence outside the customer's own
    configuration. `notice` says so in words the console shows verbatim.

    It is a `SecretStr`, so the redaction travels with the value rather than
    depending on every holder remembering: repr, str, f-strings and
    `model_dump()` all render `**********`. Only the serializer below and an
    explicit `get_secret_value()` disclose it, which makes disclosure something
    you can grep for.
    """

    api_key: ApiKeySummary
    key: SecretStr
    notice: str = SHOWN_ONCE_NOTICE

    @field_serializer("key", when_used="json")
    def _disclose_key_on_the_wire(self, key: SecretStr) -> str:
        """The one sanctioned disclosure: the create response body, once."""
        return key.get_secret_value()


class SiteKeySummary(BaseModel):
    """A public browser key, in full, every time it is asked for.

    `public_key` is a plain `str` and not a `SecretStr`, deliberately and by the
    same reasoning that made the other one a `SecretStr`: the type should say what
    the value is. This one is printed in the customer's own page source, so there
    is nothing to redact, no "shown once" to enforce, and a customer redeploying
    their site must be able to read it back. It is safe to log; it is not safe to
    confuse with a secret key, which is what `kind` is for.

    What it may do is the whole of its safety, and it is narrow: accept browser
    telemetry for `project_id`. It reads nothing.
    """

    kind: Literal["public"] = PUBLIC_KIND
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    public_key: str
    allowed_origins: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class SiteKeyListResponse(BaseModel):
    site_keys: list[SiteKeySummary]


class SiteKeyCreateRequest(BaseModel):
    """Which project the key is for, what to call it, and where it may be used.

    `allowed_origins` is required and must hold at least one entry. A public key
    with an empty allowlist would be a key the browser door accepts nothing from,
    and defaulting it to "any origin" would be worse — so the customer names the
    sites this key belongs to at the moment they mint it.
    """

    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    allowed_origins: list[str] = Field(min_length=1, max_length=MAX_ALLOWED_ORIGINS)

    @field_validator("allowed_origins")
    @classmethod
    def _origins_are_origins(cls, origins: list[str]) -> list[str]:
        """Normalized and de-duplicated, order kept, so the stored list is what
        the browser door will actually compare against."""
        seen: dict[str, None] = {}
        for candidate in origins:
            seen.setdefault(normalize_origin(candidate))
        if not seen:
            raise ValueError(NO_ORIGINS_DETAIL)
        return list(seen)
