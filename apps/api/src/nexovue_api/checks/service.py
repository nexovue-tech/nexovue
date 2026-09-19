"""Check logic: what may be saved, run, and seen.

Three gates, and none is optional.

**The project must belong to the caller.** `project_id` arrives in a request
body, which makes it a value the caller chose. It becomes a row's tenancy only
after `projects.require_project` has proved it is theirs.

**The configuration's shape is `nexovue_platform.checks.config`**, validated
before the request reaches here. What is checked here is everything that needs
the project: targets we will fetch, secrets and variables that exist, files that
belong to it, locations this deployment has (FR38, AC25).

**A check that could not be saved is refused naming every field**, not the first
one found — a form that reveals its problems one save at a time is a form people
give up on.
"""

from __future__ import annotations

import hashlib
import secrets as token_source
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.checks.config import (
    ApiCheckConfig,
    CheckConfig,
    DnsCheckConfig,
    HeartbeatCheckConfig,
    MultistepCheckConfig,
    TcpCheckConfig,
    file_ids_of,
    parse_config,
    requests_of,
    secret_names_of,
    undefined_variables,
)
from nexovue_platform.checks.maintenance import any_active
from nexovue_platform.checks.task import (
    CheckDefinition,
    FileReference,
    MaintenanceSchedule,
)
from nexovue_platform.checks.templating import has_references
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.alerts import service as alerts
from nexovue_api.audit import service as audit
from nexovue_api.checkfiles import service as files
from nexovue_api.checks import queries, targets
from nexovue_api.checks.schemas import (
    MAX_CHECKS_PER_PROJECT,
    MAX_STATE_CHANGES,
    CheckListResponse,
    CheckStateChangeItem,
    CheckStateChangeListResponse,
    CheckSummary,
    CreateCheckRequest,
    CreatedCheck,
    DeletionImpact,
    DisplayedState,
    HeartbeatToken,
    UpdateCheckRequest,
)
from nexovue_api.checksecrets import service as check_secrets
from nexovue_api.data.postgres.models import Check, CheckFile, CheckState, MaintenanceWindow
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.maintenance import service as maintenance
from nexovue_api.projects import service as projects

CANNOT_SAVE: Final = "This check cannot be saved. Each problem is named with its field."
# Run now refuses the same definitions for the same reasons, but nobody asked to
# save anything: telling them the save failed sends them looking for a save.
CANNOT_RUN: Final = "This check cannot run as written. Each problem is named with its field."

HEARTBEAT_TOKEN_BYTES: Final = 24
HEARTBEAT_PATH: Final = "/api/v1/heartbeat/"

# A single label resolves through the prober's search domains — our network,
# not the customer's — and these suffixes never mean a public name.
_PRIVATE_NAME_SUFFIXES: Final = (".internal", ".local", ".localhost", ".lan", ".home.arpa")

Problems = list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """What a definition is judged against: everything the project defines."""

    secret_names: frozenset[str]
    variables: dict[str, str]
    available_locations: list[str]


def hash_heartbeat_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- reading ------------------------------------------------------------------


async def list_for_project(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    available_locations: list[str],
) -> CheckListResponse:
    """Every check on one project, after proving the project is the caller's."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_checks(session, org_id=org_id, project_id=project_id)
    states = {
        state.check_id: state
        for state in await queries.states_in_project(session, org_id=org_id, project_id=project_id)
    }
    windows = await maintenance.schedules_by_check(
        session, org_id=org_id, project_id=project_id, check_ids=[row.id for row in rows]
    )
    now = datetime.now(UTC)
    return CheckListResponse(
        checks=[_summary(row, states.get(row.id), windows.get(row.id, []), now) for row in rows],
        available_locations=available_locations,
    )


async def get(session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID) -> CheckSummary:
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    return await _summary_of(session, check)


async def state_changes(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> CheckStateChangeListResponse:
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    rows = await queries.state_changes(
        session, org_id=org_id, check_id=check.id, limit=MAX_STATE_CHANGES
    )
    return CheckStateChangeListResponse(
        check_id=check.id,
        changes=[
            CheckStateChangeItem(
                from_state=row.from_state,
                to_state=row.to_state,
                changed_at=row.changed_at,
                run_id=row.run_id,
                location=row.location,
                reason=row.reason,
            )
            for row in rows
        ],
    )


async def deletion_impact(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> DeletionImpact:
    """What deleting loses that lives in Postgres. Runs are counted by the
    synthetics read, which is where they are stored (FR37)."""
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    return DeletionImpact(
        check_id=check.id,
        files=await files.count_for_check(session, org_id=org_id, check_id=check.id),
    )


# --- writing ------------------------------------------------------------------


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: CreateCheckRequest,
    available_locations: list[str],
) -> CreatedCheck:
    """Define a new check. The audit record commits with it or neither does."""
    project = await projects.require_project(session, org_id=org_id, project_id=request.project_id)
    existing = await queries.count_checks(session, org_id=org_id, project_id=project.id)
    if existing >= MAX_CHECKS_PER_PROJECT:
        raise ConflictError(
            f"This project already has {MAX_CHECKS_PER_PROJECT} checks, which is the limit."
        )

    context = await project_context(
        session, org_id=org_id, project_id=project.id, available_locations=available_locations
    )
    config = request.config
    await _refuse_problems(
        session,
        org_id=org_id,
        project_id=project.id,
        config=config,
        context=context,
        locations=request.locations,
        degraded_ms=request.degraded_ms,
        failing_ms=request.failing_ms,
        check_id=None,
    )

    heartbeat_token = _new_token() if isinstance(config, HeartbeatCheckConfig) else None
    check = Check(
        org_id=org_id,
        project_id=project.id,
        name=request.name,
        type=config.type,
        interval_seconds=request.interval_seconds,
        config=config.model_dump(mode="json"),
        enabled=request.enabled,
        locations=_stored_locations(config, request.locations),
        degraded_ms=request.degraded_ms,
        failing_ms=request.failing_ms,
        retries=request.retries,
        heartbeat_token_hash=hash_heartbeat_token(heartbeat_token) if heartbeat_token else None,
    )
    queries.add_check(session, check)
    await session.flush()
    await session.refresh(check)
    await files.attach(session, org_id=org_id, check_id=check.id, file_ids=file_ids_of(config))

    audit.record_check_created(session, check=check, actor_user_id=actor_user_id)
    summary = await _summary_of(session, check)
    return CreatedCheck(**summary.model_dump(), heartbeat_token=heartbeat_token)


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    check_id: uuid.UUID,
    request: UpdateCheckRequest,
    available_locations: list[str],
) -> CheckSummary:
    """Edit a check in place. Omitted fields are left alone.

    Only a change to what the check *does* is re-validated. Pausing a check whose
    secret has since been deleted must still pause it.
    """
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    sent = request.model_fields_set
    stored_config = parse_config(check.config)

    config = request.config if request.config is not None else stored_config
    if config.type != check.type:
        raise InvalidRequestError(
            CANNOT_SAVE,
            errors=[{"field": "config.type", "reason": "a check's kind cannot be changed"}],
        )
    locations = request.locations if request.locations is not None else check.locations
    degraded_ms = request.degraded_ms if "degraded_ms" in sent else check.degraded_ms
    failing_ms = request.failing_ms if "failing_ms" in sent else check.failing_ms

    if sent & {"config", "locations", "degraded_ms", "failing_ms"}:
        context = await project_context(
            session,
            org_id=org_id,
            project_id=check.project_id,
            available_locations=available_locations,
        )
        await _refuse_problems(
            session,
            org_id=org_id,
            project_id=check.project_id,
            config=config,
            context=context,
            locations=locations,
            degraded_ms=degraded_ms,
            failing_ms=failing_ms,
            check_id=check.id,
        )

    if request.name is not None:
        check.name = request.name
    if request.config is not None:
        check.config = config.model_dump(mode="json")
        await files.attach(session, org_id=org_id, check_id=check.id, file_ids=file_ids_of(config))
    if request.interval_seconds is not None:
        check.interval_seconds = request.interval_seconds
    if request.locations is not None:
        check.locations = _stored_locations(config, request.locations)
    check.degraded_ms = degraded_ms
    check.failing_ms = failing_ms
    if request.retries is not None:
        check.retries = request.retries
    if request.enabled is not None:
        check.enabled = request.enabled
    check.updated_at = datetime.now(UTC)

    audit.record_check_updated(session, check=check, actor_user_id=actor_user_id)
    await session.flush()
    return await _summary_of(session, check)


async def remove(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    check_id: uuid.UUID,
) -> None:
    """Delete a check, or 404 if it is not this organization's. Its files are
    detached and swept once the deletion has committed."""
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    await files.detach_for_check(session, org_id=org_id, check_id=check.id)
    if not await queries.delete_check(session, org_id=org_id, check_id=check.id):
        raise NotFoundError("No such check.")
    await alerts.close_incidents_of_check(session, org_id=org_id, check_id=check.id)
    audit.record_check_deleted(
        session, org_id=org_id, check_id=check_id, actor_user_id=actor_user_id
    )


async def issue_heartbeat_token(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, check_id: uuid.UUID
) -> HeartbeatToken:
    """A new URL for a heartbeat; the previous one stops working at once (FR16)."""
    check = await _require_check(session, org_id=org_id, check_id=check_id)
    if check.type != "heartbeat":
        raise InvalidRequestError("Only a heartbeat check has a URL to call.")
    token = _new_token()
    check.heartbeat_token_hash = hash_heartbeat_token(token)
    check.updated_at = datetime.now(UTC)
    audit.record_check_heartbeat_token_issued(session, check=check, actor_user_id=actor_user_id)
    return HeartbeatToken(token=token, ping_path=f"{HEARTBEAT_PATH}{token}")


# --- what a run is handed ---------------------------------------------------------


async def project_context(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    available_locations: list[str],
) -> ProjectContext:
    return ProjectContext(
        secret_names=await check_secrets.secret_names(
            session, org_id=org_id, project_id=project_id
        ),
        variables=await check_secrets.variable_values(
            session, org_id=org_id, project_id=project_id
        ),
        available_locations=available_locations,
    )


async def run_now_definition(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    config: CheckConfig,
    degraded_ms: int | None,
    failing_ms: int | None,
    location: str,
    available_locations: list[str],
) -> CheckDefinition:
    """A definition that has not been saved, validated as a save would be.

    The files may belong to the check being edited or to none yet; what matters
    is that they are this project's.
    """
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    if isinstance(config, HeartbeatCheckConfig):
        raise InvalidRequestError(
            "A heartbeat is not probed: it waits for your job to call it, so there is "
            "nothing to run now."
        )
    context = await project_context(
        session, org_id=org_id, project_id=project_id, available_locations=available_locations
    )
    problems = _definition_problems(
        config=config,
        context=context,
        locations=[location],
        degraded_ms=degraded_ms,
        failing_ms=failing_ms,
    )
    if problems:
        raise InvalidRequestError(CANNOT_RUN, errors=problems)

    file_rows = await files.validate_attachable(
        session,
        org_id=org_id,
        project_id=project_id,
        file_ids=file_ids_of(config),
        check_id=None,
        in_use_allowed=True,
    )
    return CheckDefinition(
        check_id=None,
        org_id=str(org_id),
        project_id=str(project_id),
        name=name,
        type=config.type,
        config=config.model_dump(mode="json"),
        interval_seconds=60,
        locations=[location],
        degraded_ms=degraded_ms,
        failing_ms=failing_ms,
        retries=1,
        variables=context.variables,
        sealed_secrets=await check_secrets.sealed_secrets(
            session, org_id=org_id, project_id=project_id, names=secret_names_of(config)
        ),
        files=file_references(file_rows.values()),
        maintenance=[],
    )


def maintenance_schedules(windows: Sequence[MaintenanceWindow]) -> list[MaintenanceSchedule]:
    return [
        MaintenanceSchedule(
            starts_at=window.starts_at.isoformat(),
            ends_at=window.ends_at.isoformat(),
            recurrence=window.recurrence,
            repeat_until=window.repeat_until.isoformat() if window.repeat_until else None,
        )
        for window in windows
    ]


# --- validation -----------------------------------------------------------------


async def _refuse_problems(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    config: CheckConfig,
    context: ProjectContext,
    locations: list[str],
    degraded_ms: int | None,
    failing_ms: int | None,
    check_id: uuid.UUID | None,
) -> None:
    problems = _definition_problems(
        config=config,
        context=context,
        locations=locations,
        degraded_ms=degraded_ms,
        failing_ms=failing_ms,
    )
    if problems:
        raise InvalidRequestError(CANNOT_SAVE, errors=problems)
    await files.validate_attachable(
        session,
        org_id=org_id,
        project_id=project_id,
        file_ids=file_ids_of(config),
        check_id=check_id,
    )


def _definition_problems(
    *,
    config: CheckConfig,
    context: ProjectContext,
    locations: list[str],
    degraded_ms: int | None,
    failing_ms: int | None,
) -> Problems:
    problems: Problems = []
    if isinstance(config, ApiCheckConfig | MultistepCheckConfig):
        for prefix, request in zip(_request_prefixes(config), requests_of(config), strict=True):
            problems.extend(_url_problems(prefix, request.url))
            problems.extend(
                {"field": prefix, "reason": f"no secret named {name} in this project"}
                for name in sorted(request.secret_names() - context.secret_names)
            )
        prefixes = _request_prefixes(config)
        problems.extend(
            {"field": prefixes[position], "reason": f"no variable named {name} is defined"}
            for position, name in undefined_variables(config, frozenset(context.variables))
        )
    elif isinstance(config, TcpCheckConfig):
        problems.extend(_refusal("config.host", targets.validate_host, config.host))
    elif isinstance(config, DnsCheckConfig):
        problems.extend(_name_problems("config.name", config.name))
        if config.resolver is not None:
            problems.extend(_refusal("config.resolver", targets.validate_resolver, config.resolver))

    if isinstance(config, HeartbeatCheckConfig):
        if degraded_ms is not None or failing_ms is not None:
            problems.append(
                {"field": "degraded_ms", "reason": "a heartbeat has no response time to judge"}
            )
    else:
        unknown = sorted(set(locations) - set(context.available_locations))
        problems.extend(
            {"field": "locations", "reason": f"there is no probe location called {name}"}
            for name in unknown
        )
    if degraded_ms is not None and failing_ms is not None and degraded_ms >= failing_ms:
        problems.append({"field": "failing_ms", "reason": "must be above the degraded threshold"})
    return problems


def _request_prefixes(config: ApiCheckConfig | MultistepCheckConfig) -> list[str]:
    if isinstance(config, ApiCheckConfig):
        return ["config.request"]
    return [f"config.steps.{position}" for position in range(len(config.steps))]


def _url_problems(prefix: str, url: str) -> Problems:
    """A literal URL is judged now. One whose host is a `{{variable}}` is judged by
    the connection guard when it runs, which is the real boundary anyway."""
    if not has_references(url):
        return _refusal(f"{prefix}.url", targets.validate_target, url)
    scheme, _, rest = url.partition("://")
    host = rest.split("/", 1)[0]
    if not _ or has_references(scheme) or has_references(host):
        return []
    return _refusal(f"{prefix}.url", targets.validate_target, f"{scheme}://{host}/")


def _name_problems(field: str, name: str) -> Problems:
    lowered = name.lower().rstrip(".")
    if "." not in lowered or lowered.endswith(_PRIVATE_NAME_SUFFIXES):
        return [{"field": field, "reason": "must be a public domain name"}]
    return _refusal(field, targets.validate_host, name)


def _refusal(field: str, validate: Callable[[str], str], value: str) -> Problems:
    try:
        validate(value)
    except InvalidRequestError as refused:
        return [{"field": field, "reason": refused.detail}]
    return []


# --- helpers ---------------------------------------------------------------------


def _stored_locations(config: CheckConfig, requested: list[str]) -> list[str]:
    """Empty means every location. A heartbeat is called, not probed, so none."""
    if isinstance(config, HeartbeatCheckConfig):
        return []
    return list(dict.fromkeys(requested))


def _new_token() -> str:
    return token_source.token_urlsafe(HEARTBEAT_TOKEN_BYTES)


def file_references(rows: Iterable[CheckFile]) -> dict[str, FileReference]:
    return {
        str(row.id): FileReference(
            name=row.name, content_type=row.content_type, size_bytes=row.size_bytes
        )
        for row in rows
    }


async def _require_check(session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID) -> Check:
    """The check, or a 404 that does not say whether it exists elsewhere."""
    check = await queries.find_check(session, org_id=org_id, check_id=check_id)
    if check is None:
        raise NotFoundError("No such check.")
    return check


async def _summary_of(session: AsyncSession, check: Check) -> CheckSummary:
    state = await queries.find_state(session, org_id=check.org_id, check_id=check.id)
    windows = await maintenance.schedules_by_check(
        session, org_id=check.org_id, project_id=check.project_id, check_ids=[check.id]
    )
    return _summary(check, state, windows.get(check.id, []), datetime.now(UTC))


def _summary(
    check: Check,
    state: CheckState | None,
    windows: list[MaintenanceWindow],
    now: datetime,
) -> CheckSummary:
    in_maintenance = any_active([maintenance.schedule_of(window) for window in windows], now)
    confirmed = state.state if state else None
    displayed: DisplayedState | None
    if not check.enabled:
        displayed = "paused"
    elif in_maintenance:
        displayed = "maintenance"
    else:
        displayed = confirmed  # type: ignore[assignment]  # CHECK constraint holds it
    return CheckSummary(
        id=check.id,
        project_id=check.project_id,
        name=check.name,
        type=check.type,
        config=parse_config(check.config),
        interval_seconds=check.interval_seconds,
        locations=check.locations,
        degraded_ms=check.degraded_ms,
        failing_ms=check.failing_ms,
        retries=check.retries,
        enabled=check.enabled,
        confirmed_state=confirmed,
        state_since=state.since if state else None,
        last_run_at=state.last_run_at if state else None,
        in_maintenance=in_maintenance,
        displayed_state=displayed,
        last_ping_at=check.last_ping_at,
        created_at=check.created_at,
        updated_at=check.updated_at,
    )
