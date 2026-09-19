"""Send test (FR3). Its own prefix so it has its own rate quota: every call makes our
infrastructure contact an address the customer chose."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayAdminister
from nexovue_api.channels import service
from nexovue_api.channels.schemas import SendTestAccepted, SendTestResult
from nexovue_api.deps import ChannelTesterDep, DbSession

router = APIRouter(prefix="/channel-tests", tags=["alerts"])


class SendTestRequest(BaseModel):
    channel_id: uuid.UUID


@router.post("", status_code=status.HTTP_202_ACCEPTED, summary="Send a test through a channel")
async def send_test(
    identity: MayAdminister, db: DbSession, tester: ChannelTesterDep, request: SendTestRequest
) -> SendTestAccepted:
    return await service.dispatch_test(
        db, tester, org_id=identity.org_id, channel_id=request.channel_id
    )


@router.get("/{request_id}", summary="What a send test did, waiting briefly for it")
async def send_test_result(
    identity: CurrentIdentity,
    tester: ChannelTesterDep,
    request_id: str,
    wait_seconds: Annotated[float, Query(ge=0, le=service.MAX_TEST_WAIT_SECONDS)] = 0,
) -> SendTestResult:
    return await service.test_result(
        tester, org_id=identity.org_id, request_id=request_id, wait_seconds=wait_seconds
    )
