"""What the billing webhook answers with.

Deliberately tiny and deliberately not the event. The body goes back to a
payment processor, not to a person, and the only thing it can usefully say is
what happened to the delivery — anything more would be this application telling
an unauthenticated caller what it knows about a tenant.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DeliveryOutcome(StrEnum):
    """What became of one signed delivery.

    All three are a **200**, which is the difference between this door and the
    deploy webhook's. A processor delivering at least once retries anything that
    is not a 200, so answering a duplicate with a 409 — as `deploys` correctly
    does for a CI system that retries on failure — would ask for the same
    already-handled event forever.
    """

    RECORDED = "recorded"
    # Seen before. The unique index means it changed nothing the second time.
    DUPLICATE = "duplicate"
    # Correctly signed, and about something this product does not act on.
    IGNORED = "ignored"


class DeliveryAcknowledgement(BaseModel):
    outcome: DeliveryOutcome
