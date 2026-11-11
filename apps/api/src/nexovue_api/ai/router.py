"""`/ai`: an organization's AI settings (F3 FR23 to FR32; design *API*). HTTP only.

Reading whether AI is on is every person's; everything else is `MANAGE_AI` (an owner or an
admin), and agreeing to send facts is the owner's own act (`CONSENT_AI_DISCLOSURE`, AC32).
Postgres only, so on the default rate-limit tier.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.ai import service
from nexovue_api.ai.schemas import (
    AISettings,
    CheckAccepted,
    CheckResult,
    ConsentRequest,
    Preview,
    ProjectOptOut,
    RunRow,
    SaveConnection,
    SentView,
    SwitchRequest,
    SwitchState,
    Usage,
)
from nexovue_api.auth.dependencies import CurrentIdentity, Identity
from nexovue_api.auth.permissions import Action, may
from nexovue_api.auth.policy import MayConsentAIDisclosure, MayManageAI
from nexovue_api.deps import AppSettings, DbSession, OptionalEmail
from nexovue_api.settings import Settings

router = APIRouter(prefix="/ai", tags=["ai"])


def availability_of(settings: Settings) -> service.Availability:
    return service.Availability(
        deployment=settings.nexovue_deployment,
        keys=settings.ai_keys,
        insight_replicas=settings.scheduler_insight_replicas,
        app_base_url=settings.app_base_url,
    )


def _actor(identity: Identity) -> service.Actor:
    return service.Actor(
        org_id=identity.org_id,
        user_id=identity.user.id,
        organization_name=identity.organization.name,
    )


@router.get("/connection", summary="Whether AI wording is on here, and for admins, its setup")
async def read_connection(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings
) -> AISettings:
    return await service.settings_view(
        db,
        org_id=identity.org_id,
        # A support view reads on or off only: the address may carry a customer's secret path.
        may_manage=identity.impersonation is None and may(identity.user.role, Action.MANAGE_AI),
        availability=availability_of(settings),
    )


@router.put("/connection", summary="Save the provider connection")
async def save_connection(
    identity: MayManageAI,
    db: DbSession,
    settings: AppSettings,
    email: OptionalEmail,
    request: SaveConnection,
) -> AISettings:
    return await service.save_connection(
        db,
        actor=_actor(identity),
        request=request,
        availability=availability_of(settings),
        email=email,
    )


@router.delete(
    "/connection",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the connection: erase the key, end consent, turn AI off",
)
async def delete_connection(
    identity: MayManageAI, db: DbSession, settings: AppSettings, email: OptionalEmail
) -> None:
    await service.delete_connection(
        db, actor=_actor(identity), availability=availability_of(settings), email=email
    )


@router.post(
    "/connection/check",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Test the connection, as a job on the AI container",
)
async def start_check(identity: MayManageAI, db: DbSession, settings: AppSettings) -> CheckAccepted:
    return await service.start_check(
        db, actor=_actor(identity), availability=availability_of(settings)
    )


@router.get("/connection/check/{request_id}", summary="What a connection test found")
async def check_result(identity: MayManageAI, db: DbSession, request_id: uuid.UUID) -> CheckResult:
    return await service.check_result(db, org_id=identity.org_id, request_id=request_id)


@router.get("/preview", summary="The exact request for the newest finding's facts")
async def read_preview(identity: MayManageAI, db: DbSession) -> Preview:
    return await service.preview(db, org_id=identity.org_id)


@router.post("/consent", summary="An owner agrees to send these facts to this address")
async def consent(
    identity: MayConsentAIDisclosure,
    db: DbSession,
    settings: AppSettings,
    request: ConsentRequest,
) -> AISettings:
    return await service.consent(
        db,
        actor=_actor(identity),
        fingerprint=request.fingerprint,
        schema_version=request.schema_version,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@router.put("/switch", summary="Turn AI wording on or off")
async def switch(
    identity: MayManageAI, db: DbSession, settings: AppSettings, request: SwitchRequest
) -> SwitchState:
    return await service.switch(
        db, actor=_actor(identity), on=request.on, availability=availability_of(settings)
    )


@router.get("/project-opt-outs", summary="Each project, and whether it is kept out")
async def read_opt_outs(identity: MayManageAI, db: DbSession) -> list[ProjectOptOut]:
    return await service.project_opt_outs(db, org_id=identity.org_id)


@router.put("/project-opt-outs/{project_id}", summary="Keep a project out of AI wording")
async def opt_out(
    identity: MayManageAI, db: DbSession, settings: AppSettings, project_id: uuid.UUID
) -> ProjectOptOut:
    return await service.set_opt_out(
        db,
        actor=_actor(identity),
        project_id=project_id,
        opted_out=True,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@router.delete("/project-opt-outs/{project_id}", summary="Let a project back into AI wording")
async def opt_in(
    identity: MayManageAI, db: DbSession, settings: AppSettings, project_id: uuid.UUID
) -> ProjectOptOut:
    return await service.set_opt_out(
        db,
        actor=_actor(identity),
        project_id=project_id,
        opted_out=False,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@router.get("/usage", summary="This month's tokens, the limit and the reset date")
async def read_usage(identity: MayManageAI, db: DbSession) -> Usage:
    return await service.usage(db, org_id=identity.org_id, now=datetime.now(UTC))


@router.get("/runs", summary="Each model call: when, where, tokens, outcome; never content")
async def read_runs(identity: MayManageAI, db: DbSession) -> list[RunRow]:
    return await service.run_list(db, org_id=identity.org_id)


@router.get("/runs/{run_id}/sent", summary="What a run sent, rebuilt, and whether it matches")
async def read_sent(identity: MayManageAI, db: DbSession, run_id: uuid.UUID) -> SentView:
    return await service.sent(db, org_id=identity.org_id, run_id=run_id)
