"""The connector shapes the UI reads and writes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

# What the scheduler has a poller for. A kind it cannot run is refused here
# rather than stored and silently never polled.
IntegrationKind = Literal["cloudwatch", "postgres"]

MIN_INTERVAL_SECONDS: Final = 60
MAX_INTERVAL_SECONDS: Final = 86_400
DEFAULT_INTERVAL_SECONDS: Final = 300


class IntegrationSummary(BaseModel):
    """One connector. Contains no secret, because there is none to contain."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: IntegrationKind
    config: dict[str, Any]
    # Ours, generated at creation. Shown because the customer must paste it into
    # their own trust policy — it is not a credential, and it is useless to
    # anyone who cannot also change that policy.
    external_id: uuid.UUID | None
    interval_seconds: int
    status: str
    # Empty while healthy. This is what turns "no data" into "your trust policy
    # does not name our account", which is the difference between a customer
    # who can fix it and one who files a ticket.
    last_error: str | None
    last_sync_at: datetime | None
    created_at: datetime


class IntegrationListResponse(BaseModel):
    integrations: list[IntegrationSummary]


class CreateIntegrationRequest(BaseModel):
    """A new connector.

    There is deliberately no `external_id` field. AWS requires that value to be
    unguessable and not chosen by the caller, so accepting one would hand the
    customer the very thing it protects against.
    """

    project_id: uuid.UUID
    kind: IntegrationKind
    config: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = Field(
        default=DEFAULT_INTERVAL_SECONDS, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS
    )


class CreatedIntegrationResponse(BaseModel):
    """What the customer needs to finish setting the connector up.

    The ExternalId appears here and in the summary rather than only once: the
    customer has to put it in their trust policy, and a value they cannot read
    back is a connector they cannot repair.
    """

    integration: IntegrationSummary
    # The exact condition to place in the role's trust policy. Rendered here so
    # the customer is not left translating a concept into JSON themselves.
    trust_policy_condition: dict[str, Any]
