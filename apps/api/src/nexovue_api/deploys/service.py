"""Deploy logic: what is accepted from the internet, and who may read it back.

The webhook is the only public write in the control plane, so the order of the
steps is the security property (F28 FR5):

1. open the project's live deploy secrets — one statement, tenancy taken from
   the secret row and never from the request;
2. verify the signature over the **raw** bytes;
3. only then parse them, check every item, and write.

Nothing is written before step 2 holds, and an unknown project, a project with
no live secret, a missing signature and a wrong signature are all refused with
the same words — otherwise the endpoint answers "is this project id real?" for
anyone who asks.

**After the signature** (design 3, *From the webhook*). A refusal of a field is
recorded where the Deploys section shows it, since the sender proved the secret
(D22). Repeats are answered with what they stored and go no further (FR7). Only new
items count against the hourly limit, charged before anything is stored, so a
refused batch charges nothing (FR13). New deploys are written in the sync's order,
service then version, each filed under its release, and the periods they call for
are placed (`releases.service`). One audit entry names the batch.

Correlating a deploy with telemetry lives here too, as `DeployHistory` — but
only the deploy half of it. Nothing in this module reaches for a span: it
answers "which deploy came last before this instant" and leaves whoever holds
the telemetry to ask.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform import releases as release_rules
from nexovue_platform.checks.sealing import (
    DEPLOY_WEBHOOK_PURPOSE,
    ConfiguredKeys,
    SealedBox,
    SealError,
    deploy_webhook_owner,
    keyring_from,
)
from nexovue_platform.masking import mask_flat
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import DeployEvent, ProjectWebhookSecret
from nexovue_api.deploys import queries, signatures
from nexovue_api.deploys.schemas import (
    CurrentVersion,
    DeployAnswer,
    DeployBatch,
    DeployBatchAnswer,
    DeployEventListResponse,
    DeployEventSummary,
    DeployItem,
    LastRefusal,
    MintedWebhookSecret,
    MintedWebhookSecretResponse,
    WebhookSecret,
    WebhookSecretsResponse,
)
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
    RateLimitExceededError,
    UnauthenticatedError,
)
from nexovue_api.projects import service as projects
from nexovue_api.ratelimit.limiter import Decision
from nexovue_api.ratelimit.policy import DEPLOYS_ACCEPTED
from nexovue_api.releases import queries as release_queries
from nexovue_api.releases import service as releases

logger = get_logger(__name__)

# One sentence for every way a caller failed to prove itself. It names no
# project and distinguishes no cause on purpose.
REFUSED_DETAIL: Final = (
    f"The request was not signed by this project's deploy secret. "
    f"Send {signatures.SIGNATURE_HEADER}: sha256=<hex HMAC-SHA256 of the exact body>."
)

NOT_JSON_DETAIL: Final = (
    'The signed body must be a JSON object: {"deploys": [ ... ]}, with an optional "dry_run": true.'
)
UNKNOWN_FIELD: Final = "unknown field"
UNKNOWN_FIELD_DETAIL: Final = "is not a field we accept; check its spelling"
NOT_AN_ITEM_DETAIL: Final = "must be an object of deploy fields"
DUPLICATE_DETAIL: Final = "appears twice in this body (item {first}); each deploy needs its own"

# FR8 (starting guesses): a claimed time this far ahead of our clock, or behind it, is
# refused. It also ends a replay of any capture older than a week.
AHEAD_LIMIT: Final = timedelta(minutes=5)
BEHIND_LIMIT: Final = timedelta(days=7)
TIME_DETAIL: Final = (
    "must be at most 5 minutes ahead of our clock and at most 7 days behind it; "
    "our clock reads {now}"
)

DRY_RUN_MESSAGE: Final = "valid, not stored"
STORED_MESSAGE: Final = "stored"
LIMIT_DETAIL: Final = (
    f"This project may store {DEPLOYS_ACCEPTED.limit:,} new deploys an hour, and this batch "
    "would pass that. Nothing was stored; send it again after the time in Retry-After."
)
# A project may hold this many live secrets (FR4, a starting guess): each request opens
# every one before any signature has proved anything.
MAX_LIVE_SECRETS: Final = 5
TOO_MANY_SECRETS_DETAIL: Final = (
    f"This project already has {MAX_LIVE_SECRETS} live deploy secrets. Withdraw one you no "
    "longer use before minting another: every request is checked against each live secret."
)
NO_SUCH_SECRET: Final = "No such deploy secret."
NO_SUCH_DEPLOY: Final = "No such deploy."
KEY_MISSING_DETAIL: Final = (
    "Deploy secrets are not available on this deployment, so this could not be completed. "
    "Nothing has changed."
)
# How many of a service's seen versions an answer names (FR12).
RECENT_VERSIONS: Final = 5

# Path shown back to a customer configuring the other end. The router owns the
# real route; this is the same string, spelled where the response is built.
WEBHOOK_PATH_TEMPLATE: Final = "/api/v1/deploys/webhook/{project_id}"

# Spends a batch's worth of the project's hourly allowance, or refuses (FR13).
Charge = Callable[[int], Awaitable[Decision]]


def secret_box(keys: ConfiguredKeys) -> SealedBox:
    """The box deploy secrets are sealed in, or a 503 naming nothing about the key.

    The credential key, which only the API holds (design 2). Unconfigured, nothing can
    be minted or verified, so both refuse rather than fall back to clear text.
    """
    if not keys.current:
        raise DependencyUnavailableError(KEY_MISSING_DETAIL)
    try:
        return SealedBox(keyring_from(keys.current, keys.previous), purpose=DEPLOY_WEBHOOK_PURPOSE)
    except (ValueError, TypeError) as unusable:
        raise DependencyUnavailableError(KEY_MISSING_DETAIL) from unusable


# ------------------------------------------------------------------ the webhook


class _RefusedBodyError(Exception):
    """A signed body that is not acceptable, naming where (FR10)."""

    def __init__(self, position: int | None, field: str | None, reason: str) -> None:
        super().__init__(reason)
        self.position = position
        self.field = field
        self.reason = reason

    @property
    def detail(self) -> str:
        where = []
        if self.position is not None:
            where.append(f"Item {self.position}")
        if self.field is not None:
            where.append(f"field `{self.field}`")
        return f"{', '.join(where)}: {self.reason}" if where else self.reason


async def record_from_webhook(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    body: bytes,
    signature: str | None,
    box: SealedBox,
    charge: Charge,
    now: datetime | None = None,
) -> DeployBatchAnswer:
    """Store a batch of deploys, if and only if it was signed for this project.

    The audit entry joins this transaction, so stored deploys without a trail of
    their arrival is not a state this can reach.
    """
    live = await queries.live_webhook_secrets(session, project_id=project_id)
    opened = _openable(box, live)
    matched = signatures.matching_secret(
        body=body, header_value=signature, secrets=[plain for _, plain in opened]
    )
    if matched is None:
        raise UnauthenticatedError(REFUSED_DETAIL)
    # The organization is read off the secret that matched, never off the request:
    # the caller has proved only that it holds this project's secret.
    secret = opened[matched][0]
    org_id = secret.org_id
    await queries.mark_secret_used(session, org_id=org_id, secret_id=secret.id)

    try:
        batch = _parse(body, now=now or datetime.now(UTC))
    except _RefusedBodyError as refusal:
        await queries.record_refusal(
            session,
            org_id=org_id,
            project_id=project_id,
            position=refusal.position,
            field=refusal.field,
            secret_id=secret.id,
        )
        # Committed before the 422 unwinds the request, which would roll it back: the
        # refusal has no write to be atomic with (the precedent is
        # `auth.service._record_refusal`).
        await session.commit()
        raise InvalidRequestError(
            refusal.detail, position=refusal.position, field=refusal.field
        ) from None

    services = sorted({item.service_name for item in batch.deploys})
    known = await queries.known_services(
        session, org_id=org_id, project_id=project_id, services=services
    )
    seen = await queries.seen_versions(
        session, org_id=org_id, project_id=project_id, services=services
    )
    if batch.dry_run:
        await queries.record_check(session, org_id=org_id, project_id=project_id)
        return DeployBatchAnswer(
            dry_run=True,
            stored=0,
            message=DRY_RUN_MESSAGE,
            deploys=[_answer(item, known, seen) for item in batch.deploys],
        )

    stored = await _store(
        session,
        org_id=org_id,
        project_id=project_id,
        secret_id=secret.id,
        batch=batch,
        charge=charge,
    )
    await queries.record_deploy_arrival(session, org_id=org_id, project_id=project_id)
    return DeployBatchAnswer(
        dry_run=False,
        stored=len(stored.new),
        message=STORED_MESSAGE,
        deploys=[
            _answer(
                item,
                known,
                seen,
                stored.events[item.delivery_id],
                repeated=item.delivery_id not in stored.new,
            )
            for item in batch.deploys
        ],
    )


@dataclass(frozen=True, slots=True)
class _Stored:
    # Every item's deploy id and release id, by delivery id.
    events: dict[str, tuple[uuid.UUID, uuid.UUID | None]]
    # The delivery ids this request stored.
    new: frozenset[str]


async def _store(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    secret_id: uuid.UUID,
    batch: DeployBatch,
    charge: Charge,
) -> _Stored:
    # One order for every writer, the sync's, so row locks are always taken alike (AC51).
    items = sorted(batch.deploys, key=lambda one: (one.service_name, one.version, one.delivery_id))
    already = await queries.recorded_deliveries(
        session,
        org_id=org_id,
        project_id=project_id,
        delivery_ids=[item.delivery_id for item in items],
    )
    events = {key: (row.id, row.release_id) for key, row in already.items()}
    fresh = [item for item in items if item.delivery_id not in already]
    if not fresh:
        return _Stored(events=events, new=frozenset())

    decision = await charge(len(fresh))
    if not decision.allowed:
        raise RateLimitExceededError(LIMIT_DETAIL, retry_after_seconds=decision.retry_after_seconds)
    await releases.make_room(
        session,
        org_id=org_id,
        project_id=project_id,
        keys=sorted({(item.service_name, item.version) for item in fresh}),
    )

    filed: list[releases.FiledReport] = []
    new: set[str] = set()
    for item in fresh:
        deploy_id = await queries.insert_deploy(session, _row(org_id, project_id, secret_id, item))
        if deploy_id is None:
            # Another request stored this id since the read above: a repeat after all.
            # It was charged, which only a race between two copies of one body costs.
            winner = await queries.recorded_deliveries(
                session, org_id=org_id, project_id=project_id, delivery_ids=[item.delivery_id]
            )
            row = winner[item.delivery_id]
            events[item.delivery_id] = (row.id, row.release_id)
            continue
        release_id = await release_queries.save_reported_release(
            session,
            org_id=org_id,
            project_id=project_id,
            service_name=item.service_name,
            version=item.version,
            deployed_at=item.deployed_at,
            state=item.state,
        )
        await queries.file_deploy(
            session, org_id=org_id, deploy_id=deploy_id, release_id=release_id
        )
        events[item.delivery_id] = (deploy_id, release_id)
        new.add(item.delivery_id)
        # F3: an open finding this deploy came before has its facts read again.
        await queries.queue_gathers_after_deploy(
            session,
            org_id=org_id,
            project_id=project_id,
            service=item.service_name,
            deployed_at=item.deployed_at,
        )
        filed.append(
            releases.FiledReport(item.service_name, release_id, item.deployed_at, item.state)
        )

    await releases.place_periods(session, org_id=org_id, project_id=project_id, filed=filed)
    if new:
        first = next(item.delivery_id for item in batch.deploys if item.delivery_id in new)
        audit.record_deploy_batch_recorded(
            session,
            org_id=org_id,
            project_id=project_id,
            secret_id=secret_id,
            count=len(new),
            first_deploy_id=events[first][0],
        )
    return _Stored(events=events, new=frozenset(new))


def _row(
    org_id: uuid.UUID, project_id: uuid.UUID, secret_id: uuid.UUID, item: DeployItem
) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "project_id": project_id,
        "version": item.version,
        "service_name": item.service_name,
        "deployed_at": item.deployed_at,
        "source": item.source,
        "delivery_id": item.delivery_id,
        # The default credential rules, before anything stores it (FR9, AC53).
        "event_metadata": mask_flat(item.metadata),
        "state": item.state,
        "commit_sha": item.commit_sha,
        "url": item.url,
        "webhook_secret_id": secret_id,
    }


def _answer(
    item: DeployItem,
    known: set[str],
    seen: dict[str, list[str]],
    stored: tuple[uuid.UUID, uuid.UUID | None] | None = None,
    *,
    repeated: bool = False,
) -> DeployAnswer:
    versions = seen.get(item.service_name, [])
    return DeployAnswer(
        delivery_id=item.delivery_id,
        deploy_id=None if stored is None else stored[0],
        release_id=None if stored is None else stored[1],
        repeated=repeated,
        known=item.service_name in known,
        version_seen=item.version in versions,
        recent_versions=versions[:RECENT_VERSIONS],
    )


def _openable(
    box: SealedBox, live: Sequence[ProjectWebhookSecret]
) -> list[tuple[ProjectWebhookSecret, str]]:
    """Every live secret that opens. One that does not is treated as no match, and
    logged for the operator by id alone (design, *Failure handling*)."""
    opened = []
    for row in live:
        try:
            plain = box.open(
                row.sealed_secret, owner=deploy_webhook_owner(str(row.project_id), str(row.id))
            )
        except SealError:
            logger.error("deploy_secret_unreadable", secret_id=str(row.id))
            continue
        opened.append((row, plain))
    return opened


def _parse(body: bytes, *, now: datetime) -> DeployBatch:
    """The signed bytes as a batch, every item checked. Reached only after the signature
    held. One bad item refuses the whole body, naming the item and the field (FR10)."""
    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise _RefusedBodyError(None, None, NOT_JSON_DETAIL) from None
    if not isinstance(decoded, dict):
        raise _RefusedBodyError(None, None, NOT_JSON_DETAIL)
    try:
        batch = DeployBatch.model_validate(decoded)
    except ValidationError as invalid:
        raise _refusal_from(invalid) from None
    first_at: dict[str, int] = {}
    for position, item in enumerate(batch.deploys, start=1):
        if item.delivery_id in first_at:
            raise _RefusedBodyError(
                position, "delivery_id", DUPLICATE_DETAIL.format(first=first_at[item.delivery_id])
            )
        first_at[item.delivery_id] = position
        if not now - BEHIND_LIMIT <= item.deployed_at <= now + AHEAD_LIMIT:
            raise _RefusedBodyError(
                position, "deployed_at", TIME_DETAIL.format(now=now.isoformat(timespec="seconds"))
            )
    return batch


_ITEM_FIELDS: Final = frozenset(DeployItem.model_fields)
_BATCH_FIELDS: Final = frozenset(DeployBatch.model_fields)
_VALUE_ERROR_PREFIX: Final = "Value error, "
_ITEM_LOCATION: Final = 3


def _refusal_from(invalid: ValidationError) -> _RefusedBodyError:
    """The first error, as an item position and a field we know the name of. A field
    the sender invented is recorded as an unknown field, never by its own name."""
    error = invalid.errors(include_url=False)[0]
    location = error["loc"]
    reason = (
        UNKNOWN_FIELD_DETAIL
        if error["type"] == "extra_forbidden"
        else str(error["msg"]).removeprefix(_VALUE_ERROR_PREFIX)
    )
    if len(location) >= _ITEM_LOCATION and isinstance(location[1], int):
        name = str(location[2])
        return _RefusedBodyError(
            location[1] + 1, name if name in _ITEM_FIELDS else UNKNOWN_FIELD, reason
        )
    if len(location) == _ITEM_LOCATION - 1 and isinstance(location[1], int):
        return _RefusedBodyError(location[1] + 1, None, NOT_AN_ITEM_DETAIL)
    head = str(location[0]) if location else ""
    return _RefusedBodyError(None, head if head in _BATCH_FIELDS else UNKNOWN_FIELD, reason)


# ------------------------------------------------------------------ the secrets


async def webhook_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> WebhookSecretsResponse:
    """What the Deploys section shows: never a secret's value (AC4), so a read needs no
    "viewed" trail any more."""
    project = await projects.require_project(session, org_id=org_id, project_id=project_id)
    return await _secrets_response(session, org_id=org_id, project_id=project.id)


async def create_webhook_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    box: SealedBox,
) -> MintedWebhookSecretResponse:
    """Mint a secret, leaving any existing ones live, and show it this once (FR2).

    Rotation is create, update CI, then revoke — in that order. Replacing the
    secret in one step would leave a window where CI signs with something we no
    longer accept, and deploys would vanish rather than fail loudly.
    """
    project = await projects.require_project(session, org_id=org_id, project_id=project_id)
    await queries.lock_project(session, org_id=org_id, project_id=project.id)
    live = await queries.live_secrets_of(session, org_id=org_id, project_id=project.id)
    if len(live) >= MAX_LIVE_SECRETS:
        raise ConflictError(TOO_MANY_SECRETS_DETAIL)

    # The seal is bound to the row's own id, so the id is read first (design 2).
    secret_id = await queries.new_row_id(session)
    plaintext = signatures.new_secret()
    row = ProjectWebhookSecret(
        id=secret_id,
        org_id=org_id,
        project_id=project.id,
        sealed_secret=box.seal(
            plaintext, owner=deploy_webhook_owner(str(project.id), str(secret_id))
        ),
        secret_hint=signatures.secret_hint(plaintext),
    )
    queries.add_webhook_secret(session, row)
    await session.flush()
    await session.refresh(row)

    audit.record_deploy_webhook_secret_created(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    listed = await _secrets_response(session, org_id=org_id, project_id=project.id)
    return MintedWebhookSecretResponse(
        **listed.model_dump(),
        minted=MintedWebhookSecret(**_listed(row).model_dump(), secret=plaintext),
    )


async def revoke_webhook_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    secret_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    remove_since: datetime | None = None,
) -> None:
    """Withdraw one secret, and if asked, remove what it signed since a time (D37).

    Marked rather than deleted, so an audit can still see what was withdrawn and when,
    and so the deploys it signed still name it.
    """
    project_id = await queries.revoke_webhook_secret(session, org_id=org_id, secret_id=secret_id)
    if project_id is None:
        raise NotFoundError(NO_SUCH_SECRET)
    audit.record_deploy_webhook_secret_revoked(
        session, org_id=org_id, secret_id=secret_id, actor_user_id=actor_user_id
    )
    if remove_since is None:
        return
    signed = await queries.deploys_signed_since(
        session, org_id=org_id, secret_id=secret_id, since=remove_since
    )
    count = await _remove(session, org_id=org_id, project_id=project_id, deploy_ids=signed)
    audit.record_deploys_removed(
        session,
        org_id=org_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        count=count,
        deploy_id=None,
        secret_id=secret_id,
        since=remove_since,
    )


async def remove_deploy(
    session: AsyncSession, *, org_id: uuid.UUID, deploy_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """Remove one deploy, and work out its release and periods again (AC61)."""
    deploy = await queries.find_deploy(session, org_id=org_id, deploy_id=deploy_id)
    if deploy is None:
        raise NotFoundError(NO_SUCH_DEPLOY)
    project = await projects.require_project(session, org_id=org_id, project_id=deploy.project_id)
    await _remove(session, org_id=org_id, project_id=project.id, deploy_ids=[deploy.id])
    audit.record_deploys_removed(
        session,
        org_id=org_id,
        project_id=project.id,
        actor_user_id=actor_user_id,
        count=1,
        deploy_id=deploy.id,
        secret_id=None,
        since=None,
    )


async def _remove(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    deploy_ids: Sequence[uuid.UUID],
) -> int:
    if not deploy_ids:
        return 0
    removed = await queries.delete_deploys(session, org_id=org_id, deploy_ids=deploy_ids)
    await releases.recalculate(
        session,
        org_id=org_id,
        project_id=project_id,
        affected=[
            (row.service_name, row.release_id) for row in removed if row.release_id is not None
        ],
    )
    return len(removed)


async def _secrets_response(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> WebhookSecretsResponse:
    live = await queries.live_secrets_of(session, org_id=org_id, project_id=project_id)
    intake = await queries.intake_state(session, org_id=org_id, project_id=project_id)
    versions = await queries.current_versions(session, org_id=org_id, project_id=project_id)
    held = await release_queries.release_count(session, org_id=org_id, project_id=project_id)
    refusal = None
    if intake is not None and intake.last_refusal_at is not None:
        refusal = LastRefusal(
            at=intake.last_refusal_at,
            position=intake.last_refusal_position,
            field=intake.last_refusal_field,
            secret_id=intake.last_refusal_secret_id,
        )
    return WebhookSecretsResponse(
        project_id=project_id,
        webhook_path=WEBHOOK_PATH_TEMPLATE.format(project_id=project_id),
        signature_header=signatures.SIGNATURE_HEADER,
        secrets=[_listed(row) for row in live],
        last_deploy_at=None if intake is None else intake.last_deploy_at,
        last_check_at=None if intake is None else intake.last_check_at,
        last_refusal=refusal,
        current_versions=[
            CurrentVersion(
                service_name=row.service_name, version=row.version, first_seen_at=row.first_seen_at
            )
            for row in versions
        ],
        over_release_cap=held >= release_rules.RELEASES_PER_PROJECT,
    )


def _listed(row: ProjectWebhookSecret) -> WebhookSecret:
    return WebhookSecret(
        id=row.id, hint=row.secret_hint, created_at=row.created_at, last_used_at=row.last_used_at
    )


# ------------------------------------------------- the deploy before something


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
