"""Import logic: preview reads and writes nothing; confirm writes everything or nothing.

Parsing is CPU the caller chose over up to five megabytes, so it runs off the
event loop. Confirm runs in the request's one transaction, so a check refused
halfway leaves no secret, variable or check behind (FR11).
"""

from __future__ import annotations

import uuid
from typing import Final

import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.checkimports import curl, openapi, postman
from nexovue_api.checkimports.schemas import (
    ImportConfirmed,
    ImportConfirmRequest,
    ImportPlan,
    ImportPreviewRequest,
)
from nexovue_api.checks import service as checks
from nexovue_api.checks.schemas import CreateCheckRequest
from nexovue_api.checksecrets import service as check_secrets
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.projects import service as projects

CANNOT_IMPORT: Final = "These checks cannot be imported. Each problem is named below."


async def preview(
    session: AsyncSession, *, org_id: uuid.UUID, request: ImportPreviewRequest
) -> ImportPlan:
    """What an import would create, after proving the project is the caller's."""
    await projects.require_project(session, org_id=org_id, project_id=request.project_id)
    return await anyio.to_thread.run_sync(build_plan, request)


def build_plan(request: ImportPreviewRequest) -> ImportPlan:
    """The plan for one source. Pure: no store, no network."""
    match request.source:
        case "curl":
            return curl.preview(request.content)
        case "openapi":
            return openapi.preview(request.content, server_url=request.server_url)
        case "postman":
            return postman.preview(
                request.content,
                environment=request.environment,
                folders=request.postman_folders,
            )


async def confirm(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: ImportConfirmRequest,
    check_secret_key: str,
    available_locations: list[str],
) -> ImportConfirmed:
    """Variables, then secrets, then checks — each check validated as a save is."""
    project = await projects.require_project(session, org_id=org_id, project_id=request.project_id)

    existing_variables = await check_secrets.variable_values(
        session, org_id=org_id, project_id=project.id
    )
    box = check_secrets.secret_box(check_secret_key) if request.secrets else None
    existing_secrets = (
        await check_secrets.opened_secrets(
            session,
            org_id=org_id,
            project_id=project.id,
            names=frozenset(secret.name for secret in request.secrets),
            box=box,
        )
        if box is not None
        else {}
    )
    _refuse_collisions(request, existing_variables, existing_secrets)

    for variable in request.variables:
        if variable.name in existing_variables:
            continue  # the same value is already there
        await check_secrets.write_variable(
            session, org_id=org_id, project_id=project.id, name=variable.name, value=variable.value
        )
    for secret in request.secrets:
        if box is None or secret.name in existing_secrets:
            continue
        await check_secrets.write_secret(
            session,
            org_id=org_id,
            project_id=project.id,
            name=secret.name,
            value=secret.value,
            box=box,
            actor_user_id=actor_user_id,
        )

    created: list[uuid.UUID] = []
    for position, imported in enumerate(request.checks):
        try:
            check = await checks.create(
                session,
                org_id=org_id,
                actor_user_id=actor_user_id,
                request=CreateCheckRequest(
                    project_id=project.id,
                    name=imported.name,
                    config=imported.config,
                    interval_seconds=imported.interval_seconds,
                    locations=imported.locations,
                ),
                available_locations=available_locations,
            )
        except InvalidRequestError as refused:
            raise InvalidRequestError(
                CANNOT_IMPORT, errors=_located(refused, f"checks.{position}.")
            ) from refused
        created.append(check.id)

    audit.record_checks_imported(
        session, org_id=org_id, project_id=project.id, actor_user_id=actor_user_id
    )
    return ImportConfirmed(created=created)


def _refuse_collisions(
    request: ImportConfirmRequest,
    existing_variables: dict[str, str],
    existing_secrets: dict[str, str],
) -> None:
    """An import never replaces a value the project already holds under that name.

    Two imports suggest the same names — `base_url`, a token named for its host —
    and replacing one silently would re-point every check the first import made.
    The same value is not a collision; a different one is refused, by name.
    """
    errors = [
        {
            "field": f"variables.{position}.name",
            "reason": f"the project already has a variable {variable.name} with a different value",
        }
        for position, variable in enumerate(request.variables)
        if existing_variables.get(variable.name, variable.value) != variable.value
    ] + [
        {
            "field": f"secrets.{position}.name",
            "reason": f"the project already has a secret {secret.name} with a different value",
        }
        for position, secret in enumerate(request.secrets)
        if existing_secrets.get(secret.name, secret.value) != secret.value
    ]
    if errors:
        raise InvalidRequestError(
            "Some names this import would use are already taken in the project. Rename them, "
            "or change the existing values in project settings.",
            errors=errors,
        )


def _located(refused: InvalidRequestError, prefix: str) -> list[dict[str, str]]:
    """The refusal's problems, each pointing at the item in the confirm request."""
    errors = refused.extensions.get("errors")
    if not isinstance(errors, list) or not errors:
        return [{"field": prefix.rstrip("."), "reason": refused.detail}]
    return [
        {"field": f"{prefix}{error.get('field', '')}", "reason": str(error.get("reason", ""))}
        for error in errors
        if isinstance(error, dict)
    ]
