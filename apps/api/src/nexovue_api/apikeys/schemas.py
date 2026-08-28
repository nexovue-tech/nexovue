"""What the key endpoints put on the wire."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ApiKeySummary(BaseModel):
    """A key as the console shows it.

    Neither the secret nor `key_prefix` appears. The secret was shown once at
    creation and is not stored; the prefix is not shown because seven of its
    sixteen characters are the start of that secret.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeySummary]
