"""What the key endpoints put on the wire."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field, SecretStr, field_serializer

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH

# The console shows this beside the secret. It is the reason the storage is safe,
# not an apology: a key we could show twice is a key we would have to store.
SHOWN_ONCE_NOTICE: Final = (
    "This is the only time this key is shown. We store a SHA-256 hash of it and "
    "never the key itself, so nobody — including us — can read it back. Copy it "
    "into your exporter now; if you lose it, revoke this key and create another."
)


class ApiKeySummary(BaseModel):
    """A key as the console shows it.

    The secret was shown once at creation and is not stored. `key_hint` is the
    scheme plus the last four characters of the prefix — enough for a customer
    holding a leaked key to tell which row to revoke, and far short of the
    twenty-two random characters the secret is made of.
    """

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
