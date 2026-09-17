"""The one lookup a ping makes: which check does this token belong to."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Check


async def record_ping_by_token_hash(
    session: AsyncSession, *, token_hash: str, at: datetime
) -> bool:
    """Mark the check's last call, reporting whether any enabled heartbeat matched.

    Resolved by the token's hash alone, like an ingest key resolves by prefix:
    the token is what *establishes* the tenant, so it cannot be scoped by one.
    """
    statement = (
        update(Check)
        .where(
            Check.heartbeat_token_hash == token_hash,
            Check.type == "heartbeat",
            Check.enabled.is_(True),
        )
        .values(last_ping_at=at)
        .returning(Check.id)
    )
    return await session.scalar(statement) is not None
