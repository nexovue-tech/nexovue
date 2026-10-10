"""What the token endpoints put on the wire.

The secret is a `SecretStr` for the reason `apikeys/schemas.py` gives: the
redaction travels with the value, so disclosing it is something you can grep
for. It exists in the create response and nowhere else.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field, SecretStr, field_serializer

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH

SHOWN_ONCE_NOTICE: Final = (
    "This is the only time this token is shown. We store a SHA-256 hash of it and "
    "never the token itself, so nobody — including us — can read it back. Put it in "
    "your CI secrets now; if you lose it, revoke this token and create another."
)

# A token that never expired would be a credential nobody ever revisits. Two
# years is the ceiling, not the habit: the console offers 30, 90 and 365 days.
MIN_TOKEN_DAYS: Final = 1
MAX_TOKEN_DAYS: Final = 730


class ApiTokenSummary(BaseModel):
    """A token as the console shows it.

    `token_hint` is the prefix — `nvt_live_` plus the first characters of the
    secret — which is what tells two tokens apart in a list and what a customer
    matches against a token they found in a log. The rest was shown once.

    `project_id` is null for a token that reads every project. `created_by_name`
    is a label: it is null once that person is removed, and the token keeps
    working (BR9, AC17).
    """

    id: uuid.UUID
    name: str
    project_id: uuid.UUID | None
    token_hint: str
    created_by_name: str | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    # What this token spent today, as of the last once-a-minute write.
    requests_today: int
    # "live", "revoked" or "expired" — computed, never stored, so a token that
    # expires while nobody is looking does not need a job to change its row.
    status: str


class ApiTokenListResponse(BaseModel):
    api_tokens: list[ApiTokenSummary]


class ApiTokenCreateRequest(BaseModel):
    """What to call it, what it may read, and when it stops working.

    `project_id` omitted (or null) means every project in the organization —
    the same shape the stored row uses, so there is no separate scope word to
    keep in step.
    """

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    project_id: uuid.UUID | None = None
    expires_in_days: int = Field(ge=MIN_TOKEN_DAYS, le=MAX_TOKEN_DAYS)


class ApiTokenCreated(BaseModel):
    """A new token, with its secret — the one and only time it is on the wire."""

    api_token: ApiTokenSummary
    token: SecretStr
    notice: str = SHOWN_ONCE_NOTICE

    @field_serializer("token", when_used="json")
    def _disclose_token_on_the_wire(self, token: SecretStr) -> str:
        """The one sanctioned disclosure: the create response body, once."""
        return token.get_secret_value()
