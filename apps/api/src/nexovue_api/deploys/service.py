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

Correlating a deploy with telemetry lives here too, as `DeployHistory` — but
only the deploy half of it. Nothing in this module reaches for a span: it
answers "which deploy came last before this instant" and leaves whoever holds
the telemetry to ask.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import DeployEvent, ProjectWebhookSecret
from nexovue_api.deploys import queries, signatures
from nexovue_api.deploys.schemas import (
    DeployEventListResponse,
    DeployEventSummary,
    WebhookDeployRequest,
    WebhookSecret,
    WebhookSecretsResponse,
)
from nexovue_api.errors.exceptions import (
    InvalidRequestError,
    NotFoundError,
    UnauthenticatedError,
)
from nexovue_api.projects import service as projects

# One sentence for every way a caller failed to prove itself. It names no
# project and distinguishes no cause on purpose.
REFUSED_DETAIL: Final = (
    f"The request was not signed by this project's deploy secret. "
    f"Send {signatures.SIGNATURE_HEADER}: sha256=<hex HMAC-SHA256 of the exact body>."
)

# Asked of every sender, as the billing webhook asks it: the id is the only
# thing that tells a captured request from a genuine one.
MISSING_DELIVERY_DETAIL: Final = (
    f"Send {signatures.DELIVERY_HEADER} with an id unique to each delivery; "
    "a retry of the same delivery carries the same id."
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
    delivery_id: str | None = None,
) -> DeployEventSummary:
    """Store a deploy notification, if and only if it was signed for this project.

    The audit record joins this transaction, so a stored deploy without a trail
    of its arrival is not a state this can reach.
    """
    live = await queries.live_webhook_secrets(session, project_id=project_id)
    if not signatures.signature_matches(
        body=body, header_value=signature, secrets=[one.secret for one in live]
    ):
        raise UnauthenticatedError(REFUSED_DETAIL)

    # The organization is read off the secret that matched, never off the
    # request: the caller has proved only that it holds this project's secret.
    org_id = live[0].org_id

    # GitHub's signature carries no timestamp, so a captured delivery stays
    # valid forever; the delivery id is what tells it from a genuine one, and a
    # sender without one would record a new deploy every time it was replayed.
    if not delivery_id:
        raise InvalidRequestError(MISSING_DELIVERY_DETAIL)
    # A repeat answers with the deploy already stored — an honest retry after a
    # network failure succeeds, and a replay changes nothing.
    already = await queries.recorded_delivery(
        session, org_id=org_id, project_id=project_id, delivery_id=delivery_id
    )
    if already is not None:
        return _summary(already)

    notification = _parse(body)
    event = DeployEvent(
        org_id=org_id,
        project_id=project_id,
        version=notification.version,
        service_name=notification.service_name,
        deployed_at=notification.deployed_at or datetime.now(UTC),
        source=notification.source,
        event_metadata=notification.metadata,
        delivery_id=delivery_id,
    )
    try:
        # A savepoint, so two identical deliveries racing past the read above
        # leave the loser's transaction usable: it answers as a repeat would.
        async with session.begin_nested():
            queries.add_deploy_event(session, event)
            # Needed before the audit record and the response, both of which
            # name the row by the id Postgres assigns.
            await session.flush()
    except IntegrityError:
        winner = await queries.recorded_delivery(
            session, org_id=org_id, project_id=project_id, delivery_id=delivery_id
        )
        if winner is None:
            raise
        return _summary(winner)

    audit.record_deploy_recorded(session, event=event)
    return _summary(event)


# How many of a window's own deploys one request will correlate against. A
# project that deploys more often than this inside one window is not one this
# reading says anything useful about, and the cap is what keeps a control-plane
# read from being sized by a customer's CI rather than by us.
MAX_CORRELATED_DEPLOYS: Final = 1_000


class DeployHistory:
    """The deploys that could precede anything inside one window, ready to ask.

    Built once per request and asked once per row. The alternative is a statement
    per row, and a page of error groups holds fifty.
    """

    def __init__(
        self, deploys: Sequence[DeployEventSummary], *, known_from: datetime | None = None
    ) -> None:
        # Most recent first, so `preceding` is the first match rather than a
        # search for a maximum.
        self._deploys = sorted(deploys, key=lambda deploy: deploy.deployed_at, reverse=True)
        self._known_from = known_from

    def preceding(self, *, services: Sequence[str], at: datetime) -> DeployEventSummary | None:
        """The last deploy of one of these services at or before this instant.

        **This is the deploy *before* the thing, and never the deploy that caused
        it.** All this knows is that a change shipped and then something happened;
        an error four minutes later may have been triggered by traffic, by a
        dependency, or by a change nobody notified us of at all. The order is a
        fact and the cause is not, so every caller — and every screen reading
        what a caller returns — has to say the first and never imply the second.

        A group with no services matches nothing: there is no service whose
        deploys could have come first. An instant before `known_from` matches
        nothing either — see `history_for_window`.
        """
        if self._known_from is not None and at < self._known_from:
            return None

        wanted = set(services)
        return next(
            (
                deploy
                for deploy in self._deploys
                if deploy.deployed_at <= at and deploy.service_name in wanted
            ),
            None,
        )


EMPTY_DEPLOY_HISTORY: Final = DeployHistory([])


async def history_for_window(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    from_time: datetime,
    to_time: datetime,
) -> DeployHistory:
    """Every deploy that can precede an instant inside this window.

    Two statements, and between them the answer is exact rather than bounded by
    a lookback somebody picked. Anything asked about lies inside the window, so
    what precedes it is either a deploy inside the window, or — for each service
    — the single last deploy before the window opened. Every deploy older than
    that one is unreachable: a more recent deploy of the same service already
    stands in front of it.

    **When the window holds more deploys than the cap**, the oldest of them were
    not read, and the pre-window deploys are dropped rather than used: naming one
    of those would name a deploy that is provably not the last. `known_from`
    records how far back the reading actually reaches, and anything earlier is
    answered with nothing — which is what the field already means everywhere
    else, rather than a deploy that is the wrong one.

    The caller has already proved the project belongs to this organization; both
    statements carry `org_id` regardless, so the tenancy is in the SQL.
    """
    inside = await queries.deploy_events_between(
        session,
        org_id=org_id,
        project_id=project_id,
        from_time=from_time,
        to_time=to_time,
        limit=MAX_CORRELATED_DEPLOYS,
    )
    if len(inside) == MAX_CORRELATED_DEPLOYS:
        return DeployHistory(
            [_summary(event) for event in inside], known_from=inside[-1].deployed_at
        )

    before = await queries.latest_deploy_per_service_before(
        session, org_id=org_id, project_id=project_id, before=from_time
    )
    return DeployHistory([_summary(event) for event in (*inside, *before)])


async def list_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> DeployEventListResponse:
    """This project's deploys, after proving the project is the caller's."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_deploy_events(
        session, org_id=org_id, project_id=project_id, limit=limit
    )
    return DeployEventListResponse(
        deploys=[_summary(row) for row in rows], truncated=len(rows) >= limit
    )


async def webhook_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> WebhookSecretsResponse:
    """The secrets this project will accept a signature from.

    Reading a credential is itself worth a trail, so this is audited even though
    it changes nothing. What is recorded is that it was read, never the value.
    """
    project = await projects.require_project(session, org_id=org_id, project_id=project_id)
    live = await queries.live_webhook_secrets(session, project_id=project.id)

    audit.record_deploy_webhook_secret_viewed(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    return _secrets_response(project.id, live)


async def create_webhook_secret(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> WebhookSecretsResponse:
    """Mint a secret, leaving any existing ones live.

    Rotation is create, update CI, then revoke — in that order. Replacing the
    secret in one step would leave a window where CI signs with something we no
    longer accept, and deploys would vanish rather than fail loudly.
    """
    project = await projects.require_project(session, org_id=org_id, project_id=project_id)

    secret = ProjectWebhookSecret(
        org_id=org_id, project_id=project.id, secret=signatures.new_secret()
    )
    queries.add_webhook_secret(session, secret)
    await session.flush()

    audit.record_deploy_webhook_secret_created(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    live = await queries.live_webhook_secrets(session, project_id=project.id)
    return _secrets_response(project.id, live)


async def revoke_webhook_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    secret_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Withdraw one secret. Marked rather than deleted, so an audit can still
    see what was withdrawn and when."""
    if not await queries.revoke_webhook_secret(session, org_id=org_id, secret_id=secret_id):
        raise NotFoundError("No such webhook secret.")
    audit.record_deploy_webhook_secret_revoked(
        session, org_id=org_id, secret_id=secret_id, actor_user_id=actor_user_id
    )


def _secrets_response(
    project_id: uuid.UUID, live: Sequence[ProjectWebhookSecret]
) -> WebhookSecretsResponse:
    return WebhookSecretsResponse(
        project_id=project_id,
        webhook_path=WEBHOOK_PATH_TEMPLATE.format(project_id=project_id),
        signature_header=signatures.SIGNATURE_HEADER,
        secrets=[
            WebhookSecret(id=one.id, secret=one.secret, created_at=one.created_at) for one in live
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
