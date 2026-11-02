"""Alert rules and incidents (F21). Reads for anyone signed in; changes for every role
that operates alerts, except that an *anomaly finding* rule is an admin's, which
the service checks (F6 FR23, D11)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.alerts import service
from nexovue_api.alerts.schemas import (
    CoverageResponse,
    EvaluationStatus,
    IncidentListResponse,
    IncidentSummary,
    MuteRuleRequest,
    OpenIncidentCount,
    RuleListResponse,
    RuleSummary,
    UpdateRuleRequest,
    WriteRuleRequest,
)
from nexovue_api.auth.dependencies import CurrentIdentity, CurrentReader
from nexovue_api.auth.policy import MayOperateAlerts
from nexovue_api.deps import DbSession, EvaluationReaderDep
from nexovue_api.projects.service import refuse_out_of_scope

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/rules", summary="Every alert rule in one project")
async def list_rules(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID
) -> RuleListResponse:
    return await service.list_rules(db, org_id=identity.org_id, project_id=project_id)


@router.post("/rules", status_code=status.HTTP_201_CREATED, summary="Create an alert rule")
async def create_rule(
    identity: MayOperateAlerts, db: DbSession, request: WriteRuleRequest
) -> RuleSummary:
    return await service.create_rule(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        actor_role=identity.user.role,
        request=request,
    )


@router.get("/rules/{rule_id}", summary="One alert rule")
async def get_rule(identity: CurrentIdentity, db: DbSession, rule_id: uuid.UUID) -> RuleSummary:
    return await service.get_rule(db, org_id=identity.org_id, rule_id=rule_id)


@router.patch("/rules/{rule_id}", summary="Change an alert rule")
async def update_rule(
    identity: MayOperateAlerts, db: DbSession, rule_id: uuid.UUID, request: UpdateRuleRequest
) -> RuleSummary:
    return await service.update_rule(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        actor_role=identity.user.role,
        rule_id=rule_id,
        request=request,
    )


@router.put("/rules/{rule_id}/mute", summary="Mute a rule until a time, or unmute it")
async def mute_rule(
    identity: MayOperateAlerts, db: DbSession, rule_id: uuid.UUID, request: MuteRuleRequest
) -> RuleSummary:
    return await service.mute_rule(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        actor_role=identity.user.role,
        rule_id=rule_id,
        until=request.until,
    )


@router.delete(
    "/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an alert rule"
)
async def delete_rule(identity: MayOperateAlerts, db: DbSession, rule_id: uuid.UUID) -> None:
    await service.delete_rule(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        actor_role=identity.user.role,
        rule_id=rule_id,
    )


@router.get("/incidents", summary="Open incidents first, then the last 30 days")
async def list_incidents(
    reader: CurrentReader, db: DbSession, project_id: uuid.UUID
) -> IncidentListResponse:
    """On the read surface (FR17): a person, or one of the organization's tokens."""
    refuse_out_of_scope(reader, project_id)
    return await service.list_incidents(db, org_id=reader.org_id, project_id=project_id)


# Before `/incidents/{incident_id}`, which would otherwise claim the path and refuse it
# as a malformed id.
@router.get("/incidents/open-count", summary="How many incidents are open, for the sidebar")
async def open_incident_count(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID
) -> OpenIncidentCount:
    return await service.open_incident_count(db, org_id=identity.org_id, project_id=project_id)


@router.get("/incidents/{incident_id}", summary="One incident and who was told")
async def get_incident(
    reader: CurrentReader, db: DbSession, incident_id: uuid.UUID
) -> IncidentSummary:
    """Takes no `project_id`, so the scope is compared against the row it loads."""
    return await service.get_incident(
        db, org_id=reader.org_id, incident_id=incident_id, project_scope=reader.project_scope
    )


@router.get("/coverage/services/{service_name}", summary="What alerts on one service")
async def service_coverage(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID, service_name: str
) -> CoverageResponse:
    return await service.coverage_of_service(
        db, org_id=identity.org_id, project_id=project_id, service=service_name
    )


@router.get("/evaluation", summary="When the service conditions were last evaluated")
async def evaluation(identity: CurrentIdentity, reader: EvaluationReaderDep) -> EvaluationStatus:
    del identity  # any signed-in member may see whether alerting is current
    return await service.evaluation_status(reader)


@router.get("/coverage/checks/{check_id}", summary="What alerts on one check")
async def check_coverage(
    identity: CurrentIdentity, db: DbSession, project_id: uuid.UUID, check_id: uuid.UUID
) -> CoverageResponse:
    return await service.coverage_of_check(
        db, org_id=identity.org_id, project_id=project_id, check_id=check_id
    )
