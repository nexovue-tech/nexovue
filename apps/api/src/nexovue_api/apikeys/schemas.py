"""What the key endpoints put on the wire."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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
