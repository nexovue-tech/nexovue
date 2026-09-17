"""Recording a heartbeat's call. The scheduler's sweep decides up and down."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.checks.service import hash_heartbeat_token
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.heartbeats import queries
from nexovue_api.heartbeats.schemas import PingReceived

MAX_TOKEN_LENGTH: Final = 128


async def record_ping(session: AsyncSession, *, token: str) -> PingReceived:
    """One call. An unknown, rotated or paused token answers 404, the same for all
    three, so a guess learns nothing about which it was."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise NotFoundError("No such heartbeat.")
    if not await queries.record_ping_by_token_hash(
        session, token_hash=hash_heartbeat_token(token), at=datetime.now(UTC)
    ):
        raise NotFoundError("No such heartbeat.")
    return PingReceived()
