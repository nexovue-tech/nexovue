"""Secret and variable logic.

A secret is sealed before it reaches the session and is never opened here: the
API writes secrets and the scheduler reads them (ADR-010). What leaves this
module about a secret is its name and when it changed.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime

from nexovue_platform.checks.config import parse_config, requests_of, secret_names_of
from nexovue_platform.checks.sealing import (
    CHECK_SECRET_PURPOSE,
    SealedBox,
    SealError,
    check_secret_owner,
    decode_key,
    encode_sealed,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.checks import queries as check_queries
from nexovue_api.checksecrets import queries
from nexovue_api.checksecrets.schemas import (
    MAX_SECRETS_PER_PROJECT,
    MAX_VARIABLES_PER_PROJECT,
    CheckSecretListResponse,
    CheckSecretSummary,
    CheckVariableListResponse,
    CheckVariableSummary,
)
from nexovue_api.data.postgres.models import CheckSecret, CheckVariable
from nexovue_api.errors.exceptions import ConflictError, DependencyUnavailableError, NotFoundError
from nexovue_api.projects import service as projects

_KEY_MISSING = (
    "Check secrets are not available on this deployment, so nothing was stored. "
    "The operator needs to configure a check secret key."
)


async def _usage(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[Counter[str], Counter[str]]:
    """How many checks name each secret and each variable, counted once per check."""
    secrets: Counter[str] = Counter()
    variables: Counter[str] = Counter()
    for check in await check_queries.list_checks(session, org_id=org_id, project_id=project_id):
        try:
            config = parse_config(check.config)
        except ValidationError:
            continue
        secrets.update(secret_names_of(config))
        variables.update(
            frozenset(name for request in requests_of(config) for name in request.variable_names())
        )
    return secrets, variables


def secret_box(configured_key: str) -> SealedBox:
    """The box check secrets are sealed with, or a 503 that names no variable."""
    if not configured_key:
        raise DependencyUnavailableError(_KEY_MISSING)
    try:
        return SealedBox(decode_key(configured_key), purpose=CHECK_SECRET_PURPOSE)
    except ValueError as unusable:
        raise DependencyUnavailableError(_KEY_MISSING) from unusable


async def list_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> CheckSecretListResponse:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_secrets(session, org_id=org_id, project_id=project_id)
    used, _ = await _usage(session, org_id=org_id, project_id=project_id)
    return CheckSecretListResponse(
        secrets=[
            CheckSecretSummary(
                name=row.name,
                created_at=row.created_at,
                updated_at=row.updated_at,
                used_by_checks=used[row.name],
            )
            for row in rows
        ]
    )


async def write_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    value: str,
    box: SealedBox,
    actor_user_id: uuid.UUID,
) -> CheckSecretSummary:
    """Store or replace one secret. Replacing is the only way to change one."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    sealed = box.seal(value, owner=check_secret_owner(str(project_id), name))
    now = datetime.now(UTC)

    secret = await queries.find_secret(session, org_id=org_id, project_id=project_id, name=name)
    if secret is None:
        existing = await queries.list_secrets(session, org_id=org_id, project_id=project_id)
        if len(existing) >= MAX_SECRETS_PER_PROJECT:
            raise ConflictError(f"A project may hold at most {MAX_SECRETS_PER_PROJECT} secrets.")
        secret = CheckSecret(org_id=org_id, project_id=project_id, name=name, sealed_value=sealed)
        queries.add(session, secret)
    else:
        secret.sealed_value = sealed
        secret.updated_at = now
    await session.flush()
    await session.refresh(secret)

    audit.record_check_secret_written(
        session, org_id=org_id, secret_id=secret.id, actor_user_id=actor_user_id
    )
    return CheckSecretSummary(
        name=secret.name, created_at=secret.created_at, updated_at=secret.updated_at
    )


async def delete_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    actor_user_id: uuid.UUID,
) -> None:
    """Remove a secret. A check still using it becomes misconfigured, and says so."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    secret_id = await queries.delete_secret(
        session, org_id=org_id, project_id=project_id, name=name
    )
    if secret_id is None:
        raise NotFoundError("No such secret.")
    audit.record_check_secret_deleted(
        session, org_id=org_id, secret_id=secret_id, actor_user_id=actor_user_id
    )


async def secret_names(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> frozenset[str]:
    rows = await queries.list_secrets(session, org_id=org_id, project_id=project_id)
    return frozenset(row.name for row in rows)


async def opened_secrets(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    names: frozenset[str],
    box: SealedBox,
) -> dict[str, str]:
    """The named secrets that exist, opened — only to compare against an import's
    values, never to return. A value that no longer opens counts as different."""
    rows = await queries.list_secrets(session, org_id=org_id, project_id=project_id)
    opened: dict[str, str] = {}
    for row in rows:
        if row.name not in names:
            continue
        try:
            opened[row.name] = box.open(
                row.sealed_value, owner=check_secret_owner(str(project_id), row.name)
            )
        except SealError:
            opened[row.name] = ""
    return opened


async def sealed_secrets(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, names: frozenset[str]
) -> dict[str, str]:
    """The named secrets, still sealed, as a task carries them."""
    rows = await queries.list_secrets(session, org_id=org_id, project_id=project_id)
    return {row.name: encode_sealed(row.sealed_value) for row in rows if row.name in names}


async def list_variables(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> CheckVariableListResponse:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rows = await queries.list_variables(session, org_id=org_id, project_id=project_id)
    _, used = await _usage(session, org_id=org_id, project_id=project_id)
    return CheckVariableListResponse(
        variables=[
            CheckVariableSummary(
                name=row.name,
                value=row.value,
                updated_at=row.updated_at,
                used_by_checks=used[row.name],
            )
            for row in rows
        ]
    )


async def variable_values(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, str]:
    rows = await queries.list_variables(session, org_id=org_id, project_id=project_id)
    return {row.name: row.value for row in rows}


async def write_variable(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    value: str,
) -> CheckVariableSummary:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    variable = await queries.find_variable(session, org_id=org_id, project_id=project_id, name=name)
    if variable is None:
        existing = await queries.list_variables(session, org_id=org_id, project_id=project_id)
        if len(existing) >= MAX_VARIABLES_PER_PROJECT:
            raise ConflictError(
                f"A project may hold at most {MAX_VARIABLES_PER_PROJECT} variables."
            )
        variable = CheckVariable(org_id=org_id, project_id=project_id, name=name, value=value)
        queries.add(session, variable)
    else:
        variable.value = value
        variable.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(variable)
    return CheckVariableSummary(
        name=variable.name, value=variable.value, updated_at=variable.updated_at
    )


async def delete_variable(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, name: str
) -> None:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    if not await queries.delete_variable(session, org_id=org_id, project_id=project_id, name=name):
        raise NotFoundError("No such variable.")
