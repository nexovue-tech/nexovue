"""Deploy logic: what is accepted from the internet, and who may read it back.

The webhook is the only public write in the control plane, so the order of the
steps is the security property:

1. resolve the project's live ingest keys — one statement, tenancy taken from
   the project row and never from the request;
2. verify the signature over the **raw** bytes;
3. only then parse them, and only then write.

Nothing is written before step 2 holds, and an unknown project, a project with
no live key, a missing signature and a wrong signature are all refused with the
same words — otherwise the endpoint answers "is this project id real?" for
anyone who asks.

Phase 1 stores the deploy. Correlating it with telemetry is Phase 2, and there
is deliberately nothing here that reaches for a span.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import DeployEvent
from nexovue_api.deploys import queries, signatures
from nexovue_api.deploys.schemas import (
    DeployEventListResponse,
    DeployEventSummary,
    WebhookDeployRequest,
    WebhookSecret,
    WebhookSecretsResponse,
)
from nexovue_api.errors.exceptions import InvalidRequestError, UnauthenticatedError
from nexovue_api.projects import service as projects

# One sentence for every way a caller failed to prove itself. It names no
# project and distinguishes no cause on purpose.
REFUSED_DETAIL: Final = (
    f"The request was not signed by this project's deploy secret. "
    f"Send {signatures.SIGNATURE_HEADER}: sha256=<hex HMAC-SHA256 of the exact body>."
)

MALFORMED_BODY_DETAIL: Final = (
    "The signed body must be JSON carrying a non-empty version and service_name."
)

# Path shown back to a customer configuring the other end. The router owns the
# real route; this is the same string, spelled where the response is built.
WEBHOOK_PATH_TEMPLATE: Final = "/api/v1/deploys/webhook/{project_id}"


async def record_from_webhook(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    body: bytes,
    signature: str | None,
) -> DeployEventSummary:
    """Store a deploy notification, if and only if it was signed for this project.

    The audit record joins this transaction, so a stored deploy without a trail
    of its arrival is not a state this can reach.
    """
    keys = await queries.webhook_keys(session, project_id=project_id)
    secrets = [
        signatures.derive_secret(key_hash=key.key_hash, project_id=project_id) for key in keys
    ]
    if not signatures.signature_matches(body=body, header_value=signature, secrets=secrets):
        raise UnauthenticatedError(REFUSED_DETAIL)

    # A match means at least one secret existed, so at least one key did.
    org_id = keys[0].org_id
    notification = _parse(body)

    event = DeployEvent(
        org_id=org_id,
        project_id=project_id,
        version=notification.version,
        service_name=notification.service_name,
        deployed_at=notification.deployed_at or datetime.now(UTC),
        source=notification.source,
        event_metadata=notification.metadata,
    )
    queries.add_deploy_event(session, event)
    # Needed before the audit record and the response, both of which name the
    # row by the id Postgres assigns.
    await session.flush()

    audit.record_deploy_recorded(session, event=event)
    return _summary(event)


async def list_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> DeployEventListResponse:
    """This project's deploys, after proving the project is the caller's."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_deploy_events(
        session, org_id=org_id, project_id=project_id, limit=limit
    )
    return DeployEventListResponse(deploys=[_summary(row) for row in rows])


async def webhook_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> WebhookSecretsResponse:
    """The secrets that will be accepted for this project, for the person setting
    the webhook up.

    Reading a credential is itself worth a trail, so this is audited even though
    it changes nothing. What is recorded is that it was read, never the value.
    """
    project = await projects.require_project(session, org_id=org_id, project_id=project_id)
    keys = await queries.live_keys(session, org_id=org_id, project_id=project.id)

    audit.record_deploy_webhook_secret_viewed(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    return WebhookSecretsResponse(
        project_id=project.id,
        webhook_path=WEBHOOK_PATH_TEMPLATE.format(project_id=project.id),
        signature_header=signatures.SIGNATURE_HEADER,
        secrets=[
            WebhookSecret(
                api_key_id=key.id,
                api_key_name=key.name,
                secret=signatures.derive_secret(key_hash=key.key_hash, project_id=project.id),
            )
            for key in keys
        ],
    )


def _parse(body: bytes) -> WebhookDeployRequest:
    """The signed bytes as fields. Reached only after the signature held."""
    try:
        return WebhookDeployRequest.model_validate_json(body)
    except ValidationError as error:
        raise InvalidRequestError(MALFORMED_BODY_DETAIL) from error


def _summary(event: DeployEvent) -> DeployEventSummary:
    return DeployEventSummary(
        id=event.id,
        project_id=event.project_id,
        version=event.version,
        service_name=event.service_name,
        deployed_at=event.deployed_at,
        source=event.source,
        metadata=event.event_metadata,
    )
