"""Connector logic, and the one value the customer must never supply.

A connector is how door 4b reaches a customer's cloud account. It holds no
credential (ADR 0076): the customer creates a role their side that trusts ours,
and we keep its ARN plus an `ExternalId`.

**That ExternalId is generated here and never accepted from a request.** Its
entire value is that the caller cannot choose it — it is what stops someone
persuading us to assume a role on their behalf, which AWS calls the confused
deputy problem. A request field for it would hand that away.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import Integration
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.integrations import queries
from nexovue_api.integrations.schemas import (
    CreatedIntegrationResponse,
    CreateIntegrationRequest,
    IntegrationListResponse,
    IntegrationSummary,
)
from nexovue_api.projects import service as projects

# A connector exists before it has ever polled. `active` is what the scheduler
# looks for; nothing sets it to anything else yet, and a status that is always
# the same value is recorded here rather than hidden as a literal at the call.
STATUS_ACTIVE: Final = "active"

# The `kind` whose config this module validates. The other kind, cloudwatch,
# needs no auth check: it has one auth mode and no way to ask for another.
KIND_POSTGRES: Final = "postgres"


async def list_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> IntegrationListResponse:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_integrations(session, org_id=org_id, project_id=project_id)
    return IntegrationListResponse(integrations=[_summary(row) for row in rows])


# The one auth mode `nexovue_scheduler.pollers.postgres_connect` accepts. Restated
# here rather than imported, for the reason the scheduler restates the API's
# column names: the two deploy separately, and an API that cannot start because a
# scheduler package moved is an API serving nobody. It is a contract, so a change
# on either side must change both.
SUPPORTED_POSTGRES_AUTH: Final = "rds_iam"

# Phrased for the person reading it, not for us. It says what *is* supported and
# where the other route is, because since ADR 0100 "not supported" is the wrong
# answer — a database anywhere else is reachable through the customer's own
# collector, and a refusal that does not say so sends them away for nothing.
_UNSUPPORTED_POSTGRES_AUTH: Final = (
    "This connector reads AWS RDS PostgreSQL with IAM database authentication, "
    "which is why it needs no password from you. For a database anywhere else — "
    "self-hosted, on-premise, or another cloud — your own OpenTelemetry Collector "
    "can send the same data without a credential ever leaving your network. See "
    "the 'Connecting a database' guide."
)


def _refuse_unsupported_target(request: CreateIntegrationRequest) -> None:
    """Fail at creation rather than at the first poll.

    Without this the API accepts the connector, returns 201, and the scheduler
    discovers a minute later that it cannot authenticate — so the customer sees
    success, then a `failing` status somewhere else, with nothing at the point of
    the mistake saying what was wrong.
    """
    if request.kind != KIND_POSTGRES:
        return
    auth = str(request.config.get("auth", SUPPORTED_POSTGRES_AUTH))
    if auth != SUPPORTED_POSTGRES_AUTH:
        raise InvalidRequestError(_UNSUPPORTED_POSTGRES_AUTH)


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: CreateIntegrationRequest,
) -> CreatedIntegrationResponse:
    """Define a connector, and mint the ExternalId that guards it."""
    _refuse_unsupported_target(request)
    project = await projects.require_project(session, org_id=org_id, project_id=request.project_id)

    integration = Integration(
        org_id=org_id,
        project_id=project.id,
        kind=request.kind,
        config=request.config,
        external_id=uuid.uuid4(),
        interval_seconds=request.interval_seconds,
        status=STATUS_ACTIVE,
    )
    queries.add_integration(session, integration)
    await session.flush()

    audit.record_integration_created(session, integration=integration, actor_user_id=actor_user_id)
    return CreatedIntegrationResponse(
        integration=_summary(integration),
        trust_policy_condition=_trust_policy_condition(integration.external_id),
    )


async def remove(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    integration_id: uuid.UUID,
) -> None:
    """Delete a connector, or 404 if it is not this organization's."""
    if not await queries.delete_integration(session, org_id=org_id, integration_id=integration_id):
        raise NotFoundError("No such integration.")
    audit.record_integration_deleted(
        session, org_id=org_id, integration_id=integration_id, actor_user_id=actor_user_id
    )


def _trust_policy_condition(external_id: uuid.UUID | None) -> dict[str, Any]:
    """The condition the customer puts in their role's trust policy.

    Rendered rather than described. A customer translating "require an external
    id" into IAM JSON themselves is a customer who will get it subtly wrong, and
    a trust policy without this condition is one anybody can assume.
    """
    return {"StringEquals": {"sts:ExternalId": str(external_id)}}


def _summary(integration: Integration) -> IntegrationSummary:
    return IntegrationSummary(
        id=integration.id,
        project_id=integration.project_id,
        # The column is free text; the schema narrows it to the kinds we poll.
        kind=integration.kind,
        config=integration.config,
        external_id=integration.external_id,
        interval_seconds=integration.interval_seconds,
        status=integration.status,
        last_error=integration.last_error,
        last_sync_at=integration.last_sync_at,
        created_at=integration.created_at,
    )
