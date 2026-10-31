"""Project logic: the check every telemetry query depends on, and P1-76's writes.

`require_project` is the gate: a `project_id` from a query string is a value the
caller chose, and it becomes a filter on a telemetry query only after it has
been proved to belong to the caller's organization.

**What deleting a project means, and what it does not** (P1-76's open question).
It closes every door that lets data *in* — the two ingest keys and the deploy
webhook secret — and refuses while work is still going *out* in the project's
name, because an enabled check makes requests to somebody's systems from our
infrastructure and stopping that is the customer's decision rather than a side
effect of this endpoint. It does **not** touch telemetry already in ClickHouse.
Nothing here can: the nightly erasure names organizations and there is no
project-scoped equivalent, an `ALTER TABLE ... DELETE` cannot be recalled once
submitted, and the rows expire on their own because retention is stamped on each
one when it is written. So the stored data becomes unreadable — every
read goes through `require_project`, which refuses a deleted project — and
expires with the retention window. `ProjectDeleted` says exactly that rather than
letting "deleted" imply more.

**Both configuration documents are validated before they are stored**,
and only the masking off-switch is guarded. See `config.py` for the grammar and
why the check belongs here rather than in the worker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.detection import DEFAULT_SENSITIVITY, DEFAULT_TIMEZONE
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.alerts import defaults as alert_defaults
from nexovue_api.alerts import queries as alert_queries
from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Reader
from nexovue_api.data.postgres.models import DEFAULT_RETENTION_DAYS, Project, User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.projects import config, queries
from nexovue_api.projects.schemas import (
    MaskingConfigResponse,
    ProjectCreateRequest,
    ProjectDeleted,
    ProjectDetail,
    ProjectListResponse,
    ProjectSummary,
    ProjectUpdateRequest,
    SamplingConfigResponse,
)

# `slugs` is a leaf helper with no service of its own; deriving a project slug a
# different way from an organization slug would give the product two answers to
# the same question.
from nexovue_api.signup.slugs import disambiguate, slugify

# One sentence, whether the project belongs to another tenant, is outside a
# token's scope, or never existed: three answers would be three ways to probe.
NO_SUCH_PROJECT: Final = "No such project."

# How many suffixed forms of a derived slug are tried before giving up, matching
# `signup.service.MAX_SLUG_ATTEMPTS`.
MAX_SLUG_ATTEMPTS: Final = 100

LAST_PROJECT: Final = (
    "This is the organization's only project. An organization always has one; "
    "close the account instead if that is what you meant."
)
SLUG_TAKEN: Final = "Another project in this organization already uses that identifier."
NOTHING_TO_CHANGE: Final = "Give a name or an identifier to change."


async def list_for_organization(session: AsyncSession, org_id: uuid.UUID) -> ProjectListResponse:
    """Every live project, each carrying whether anything has ever arrived for it.

    A second statement rather than an aggregate joined onto the first, so
    `list_projects` remains the one definition of "this organization's live
    projects" and the meter read stays additive to it. Both are one index read
    apiece on `(org_id, …)`.
    """
    projects = await queries.list_projects(session, org_id)
    first_reported = await queries.first_reported_days(session, org_id)
    return ProjectListResponse(
        projects=[
            ProjectSummary(
                id=project.id,
                slug=project.slug,
                name=project.name,
                first_reported_on=first_reported.get(project.id),
            )
            for project in projects
        ]
    )


async def get(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> ProjectDetail:
    return _detail(await require_project(session, org_id=org_id, project_id=project_id))


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor: User,
    request: ProjectCreateRequest,
) -> ProjectDetail:
    """A new environment, with the two configuration documents left empty.

    Empty means the shipped defaults apply, which is the state most projects
    should stay in: masking is irreversible, so the rules that are on by default
    are only the ones that can never be legitimately needed to debug.

    Its days are its creator's local days (FR24, C15), UTC when they have no zone.
    """
    actor_user_id = actor.id
    slug = await _slug_for(session, org_id=org_id, requested=request.slug, name=request.name)
    # Written rather than left to the column defaults: a flush does not read a
    # server default back, so a row that relied on them would answer this request
    # with nulls. The values are the same constants the defaults are built
    # from, so there is one definition of each and not two.
    project = Project(
        org_id=org_id,
        slug=slug,
        name=request.name,
        retention_days=DEFAULT_RETENTION_DAYS,
        sampling_config={},
        masking_config={},
        timezone=actor.timezone or DEFAULT_TIMEZONE,
        detection_sensitivity=DEFAULT_SENSITIVITY,
    )
    session.add(project)
    await _flush(session)

    audit.record_project_created(session, project=project, actor_user_id=actor_user_id)
    # Any check down emails the owners from the project's first minute (F21, FR8).
    await alert_defaults.provision_project(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    return _detail(project)


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
    request: ProjectUpdateRequest,
) -> ProjectDetail:
    """Rename a project, change its identifier, or both."""
    if request.name is None and request.slug is None:
        raise InvalidRequestError(NOTHING_TO_CHANGE)

    project = await _lock_or_404(session, org_id=org_id, project_id=project_id)
    if request.slug is not None and request.slug != project.slug:
        await _refuse_taken_slug(session, org_id=org_id, slug=request.slug)
        project.slug = request.slug
    if request.name is not None:
        project.name = request.name

    await _flush(session)
    audit.record_project_updated(session, project=project, actor_user_id=actor_user_id)
    return _detail(project)


async def delete(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> ProjectDeleted:
    """Close a project: mark it, and withdraw every credential it holds.

    The mark alone would not be a deletion. The receiver resolves an ingest key
    without joining `projects`, and the deploy webhook resolves a secret the same
    way, so a project that is only marked goes on accepting telemetry from the
    internet under a name that has disappeared from every screen.
    """
    project = await _lock_or_404(session, org_id=org_id, project_id=project_id)
    if await queries.live_project_count(session, org_id) <= 1:
        raise ConflictError(LAST_PROJECT)
    await _refuse_while_work_is_scheduled(session, org_id=org_id, project_id=project_id)

    deleted_at = datetime.now(UTC)
    project.deleted_at = deleted_at
    await alert_queries.close_project(session, org_id=org_id, project_id=project_id, at=deleted_at)
    closed = ProjectDeleted(
        project_id=project.id,
        deleted_at=deleted_at,
        api_keys_revoked=await queries.revoke_api_keys(
            session, org_id=org_id, project_id=project_id, at=deleted_at
        ),
        site_keys_revoked=await queries.revoke_site_keys(
            session, org_id=org_id, project_id=project_id, at=deleted_at
        ),
        webhook_secrets_revoked=await queries.revoke_webhook_secrets(
            session, org_id=org_id, project_id=project_id, at=deleted_at
        ),
    )
    audit.record_project_deleted(
        session, org_id=org_id, project_id=project_id, actor_user_id=actor_user_id
    )
    return closed


async def write_sampling_config(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    document: dict[str, Any],
) -> SamplingConfigResponse:
    """Replace a project's sampling document. No guard, by decision.

    Sampling is reversible — it changes only what is kept from the next trace
    onward — and a confirmation dialogue on a reversible setting teaches people
    to click through the one that matters.
    """
    project = await _lock_or_404(session, org_id=org_id, project_id=project_id)
    project.sampling_config = config.validate_sampling_document(document)
    return SamplingConfigResponse(project_id=project.id, config=project.sampling_config)


async def write_masking_config(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
    document: dict[str, Any],
    confirm_disabling: list[str],
) -> MaskingConfigResponse:
    """Replace a project's masking document, guarding only the off-switch.

    Switching a shipped rule off is the one edit here that widens what leaves the
    customer's process, so it takes an acknowledgement naming the rule and leaves
    an audit row. Everything else — added keys, allowed values, hashing instead of
    blanking — is an ordinary form field.
    """
    project = await _lock_or_404(session, org_id=org_id, project_id=project_id)
    validated = config.validate_masking_document(document)

    already_off = config.disabled_rules(project.masking_config)
    newly_disabled = config.disabled_rules(validated) - already_off
    _require_confirmation_for(newly_disabled, confirm_disabling)

    project.masking_config = validated
    for rule in sorted(newly_disabled):
        audit.record_masking_rule_disabled(
            session,
            org_id=org_id,
            project_id=project_id,
            rule=rule,
            actor_user_id=actor_user_id,
        )
    return MaskingConfigResponse(project_id=project.id, config=project.masking_config)


async def require_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """The project, or a 404 that does not say whether it exists elsewhere."""
    project = await queries.find_project(session, org_id=org_id, project_id=project_id)
    if project is None:
        raise NotFoundError(NO_SUCH_PROJECT)
    return project


def refuse_out_of_scope(reader: Reader, project_id: uuid.UUID) -> None:
    """Refuse a project outside a token's scope, with the 404 a stranger gets.

    For routes whose service already proves the project belongs to the caller's
    organization: this adds the scope half without a second lookup.
    """
    if not reader.may_read_project(project_id):
        raise NotFoundError(NO_SUCH_PROJECT)


async def require_readable_project(
    session: AsyncSession, *, reader: Reader, project_id: uuid.UUID
) -> Project:
    """The project a read-surface caller named, within what they may read.

    A token scoped to one project asking about another gets the same 404 as a
    project that does not exist (BR11, AC15) — byte-identical, and raised before
    the lookup, so the two cannot drift apart or be told apart by timing.
    """
    if not reader.may_read_project(project_id):
        raise NotFoundError(NO_SUCH_PROJECT)
    return await require_project(session, org_id=reader.org_id, project_id=project_id)


def _require_confirmation_for(newly_disabled: frozenset[str], confirmed: list[str]) -> None:
    """The acknowledgement must name every rule being switched off, and no others.

    Refusing the extras is what keeps the guard real: a client that always sent
    every rule name would turn the confirmation into a constant, and a constant
    confirms nothing.
    """
    acknowledged = frozenset(confirmed)
    unnecessary = sorted(acknowledged - newly_disabled)
    if unnecessary:
        raise InvalidRequestError(
            f"confirm_disabling names {', '.join(repr(rule) for rule in unnecessary)}, "
            f"which this document does not switch off."
        )
    missing = sorted(newly_disabled - acknowledged)
    if missing:
        raise ConflictError(
            f"Switching off {', '.join(missing)} widens what leaves your systems and reaches "
            f"ours, and it cannot recover what was already masked. Re-send with "
            f"confirm_disabling naming {'it' if len(missing) == 1 else 'them'}.",
            requires_confirmation=missing,
        )


async def _refuse_while_work_is_scheduled(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Refuse to close a project that still has our infrastructure working for it.

    Neither the scheduler's check query nor its integration poll looks at
    `projects.deleted_at`, so a marked project would go on fetching a customer's
    URL and assuming a role in their cloud account. Disabling those silently from
    here is a side effect nobody asked this endpoint for; saying what is in the
    way is not.
    """
    checks = await queries.enabled_check_count(session, org_id=org_id, project_id=project_id)
    integrations = await queries.integration_count(session, org_id=org_id, project_id=project_id)
    if not checks and not integrations:
        return
    raise ConflictError(
        f"This project still has {checks} enabled check(s) and {integrations} integration(s) "
        f"running from our infrastructure. Remove them first — deleting the project would "
        f"leave them running with nowhere to report.",
        enabled_checks=checks,
        integrations=integrations,
    )


async def _lock_or_404(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """Every write takes the row's lock first, so two edits cannot interleave."""
    project = await queries.lock_project(session, org_id=org_id, project_id=project_id)
    if project is None:
        raise NotFoundError("No such project.")
    return project


async def _slug_for(
    session: AsyncSession, *, org_id: uuid.UUID, requested: str | None, name: str
) -> str:
    """The identifier the new project gets.

    A slug the customer typed is used as typed or refused, because silently
    suffixing something somebody chose gives them a project they did not name.
    A derived one is suffixed, because they expressed no opinion about it.
    """
    if requested is not None:
        await _refuse_taken_slug(session, org_id=org_id, slug=requested)
        return requested

    base = slugify(name)
    taken = await queries.slugs_beginning_with(session, org_id=org_id, base=base)
    for attempt in range(MAX_SLUG_ATTEMPTS):
        candidate = disambiguate(base, attempt)
        if candidate not in taken:
            return candidate
    raise ConflictError("Too many projects share that name. Choose an identifier yourself.")


async def _refuse_taken_slug(session: AsyncSession, *, org_id: uuid.UUID, slug: str) -> None:
    """`(org_id, slug)` is unique across deleted projects too, so a tombstone
    holds its identifier and the collision has to be reported rather than hidden."""
    if await queries.slug_is_taken(session, org_id=org_id, slug=slug):
        raise ConflictError(SLUG_TAKEN)


async def _flush(session: AsyncSession) -> None:
    """Flush, turning a lost race on `(org_id, slug)` into a 409 rather than a 500."""
    try:
        await session.flush()
    except IntegrityError as error:
        raise ConflictError(SLUG_TAKEN) from error


def _detail(project: Project) -> ProjectDetail:
    return ProjectDetail(
        id=project.id,
        slug=project.slug,
        name=project.name,
        retention_days=project.retention_days,
        sampling_config=project.sampling_config,
        masking_config=project.masking_config,
        created_at=project.created_at,
    )
