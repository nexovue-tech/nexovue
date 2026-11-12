"""`/assistant-quotes`: a chat answer's quotes, read again as the person (F4 T3.2). HTTP only.

**Its own prefix, on the telemetry tier** (design, *Routes*): it reads ClickHouse, while
`/assistant` stays on the default tier, so the panel's 1.5 s poll does not share a cap with
a screen's telemetry reads (AC107). The person's own answer only; another's is a 404.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nexovue_api.assistant import quotes
from nexovue_api.assistant.service import asker_of
from nexovue_api.auth.dependencies import CurrentIdentity, TenantRepository
from nexovue_api.deps import DbSession

router = APIRouter(prefix="/assistant-quotes", tags=["assistant"])


@router.get("/{message_id}", summary="Your answer's quotes, read again from what is kept")
async def read_quotes(
    identity: CurrentIdentity,
    db: DbSession,
    repository: TenantRepository,
    message_id: uuid.UUID,
) -> quotes.QuoteList:
    return await quotes.quotes(db, repository, asker=asker_of(identity), message_id=message_id)
