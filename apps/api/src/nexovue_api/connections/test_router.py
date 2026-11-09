"""A connection test (FR30). Its own prefix so it has its own rate quota, as a channel's
send test does: every call makes our infrastructure contact a tracker (T3.8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from nexovue_api.auth.policy import MayManageConnections
from nexovue_api.connections import service
from nexovue_api.connections.router import availability_of
from nexovue_api.connections.schemas import (
    ConnectionTestAccepted,
    ConnectionTestRequest,
    ConnectionTestResult,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/connection-tests", tags=["connections"])


@router.post("", status_code=status.HTTP_202_ACCEPTED, summary="Test a connection")
async def test_connection(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    request: ConnectionTestRequest,
) -> ConnectionTestAccepted:
    return await service.dispatch_test(
        db,
        org_id=identity.org_id,
        connection_id=request.connection_id,
        availability=availability_of(settings),
    )


@router.get("/{request_id}", summary="What a connection test found, waiting briefly for it")
async def connection_test_result(
    identity: MayManageConnections,
    db: DbSession,
    request_id: str,
    wait_seconds: Annotated[float, Query(ge=0, le=service.MAX_TEST_WAIT_SECONDS)] = 0,
) -> ConnectionTestResult:
    return await service.test_result(
        db, org_id=identity.org_id, request_id=request_id, wait_seconds=wait_seconds
    )
