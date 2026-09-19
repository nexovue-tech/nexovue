"""Run now (ADR-011). Its own prefix so it has its own rate quota: every run is
an outbound request from our infrastructure to an address the caller chose."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayOperateChecks
from nexovue_api.checkruns import service
from nexovue_api.checks.schemas import RunNowAccepted, RunNowRequest, RunNowResult
from nexovue_api.deps import AppSettings, DbSession, ProbeBrokerDep

router = APIRouter(prefix="/check-runs", tags=["checks"])


@router.post("", status_code=status.HTTP_202_ACCEPTED, summary="Run a definition once")
async def run_now(
    identity: MayOperateChecks,
    db: DbSession,
    settings: AppSettings,
    broker: ProbeBrokerDep,
    request: RunNowRequest,
) -> RunNowAccepted:
    return await service.dispatch(
        db,
        broker,
        org_id=identity.org_id,
        request=request,
        available_locations=settings.probe_locations,
    )


@router.get("/{request_id}", summary="What a Run now saw, waiting briefly for it")
async def run_now_result(
    identity: CurrentIdentity,
    broker: ProbeBrokerDep,
    request_id: str,
    wait_seconds: Annotated[float, Query(ge=0, le=service.MAX_WAIT_SECONDS)] = 0,
) -> RunNowResult:
    return await service.result(
        broker, org_id=identity.org_id, request_id=request_id, wait_seconds=wait_seconds
    )
