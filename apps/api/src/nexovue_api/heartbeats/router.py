"""`GET|POST /heartbeat/{token}`: the URL a customer's cron job calls on success.

No session: a job has none. The token in the path is the whole credential — shown
once, stored only as its hash, and rotatable — which is also how an ingest key
works. Its own rate tier, because it is reachable by anybody.
"""

from __future__ import annotations

from fastapi import APIRouter

from nexovue_api.deps import DbSession
from nexovue_api.heartbeats import service
from nexovue_api.heartbeats.schemas import PingReceived

router = APIRouter(prefix="/heartbeat", tags=["heartbeats"])


# Two routes rather than one `api_route` taking both methods: FastAPI derives an
# operation id once per route, so one route publishes the same id twice — the
# billing router's checkout return is split for the same reason.
@router.post("/{token}", summary="Report that a job ran")
async def receive_heartbeat(db: DbSession, token: str) -> PingReceived:
    return await service.record_ping(db, token=token)


@router.get("/{token}", summary="Report that a job ran, from a client that can only GET")
async def receive_heartbeat_by_get(db: DbSession, token: str) -> PingReceived:
    return await service.record_ping(db, token=token)
