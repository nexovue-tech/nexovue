"""Completing a person's GitHub account link (F5 design, *GitHub account link*; plan T3.17;
A18).

`connections.complete_github_user_link` opens the attempt's sealed code, exchanges it once
with the Fix App's OAuth client, asks GitHub who the token belongs to, and in one
transaction keeps the GitHub user id and login, drops the code and records the outcome.
**The person's token lives in this function's memory only**, and is never stored, logged
or put in a job. The audit entry names the Nexovue person, never the GitHub login (AC31).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.checks.sealing import ConfiguredKeys, SealError
from nexovue_platform.connections import connection_secret_owner
from nexovue_platform.fixes.records import (
    COMPLETE_GITHUB_USER_LINK_TASK,
    FIX_APP_CLIENT_SECRET_PURPOSE,
    GITHUB_ACCOUNT_LINKED,
    GITHUB_LINK_CODE_PURPOSE,
    LINK_FAILED,
    LINK_LINKED,
    LINK_REFUSED,
    github_link_code_owner,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections.fix_github import (
    FixEndpoints,
    GitHubFailure,
    exchange_code,
    user_of_token,
)
from nexovue_scheduler.connections.source_pack import endpoints as fix_endpoints
from nexovue_scheduler.connections.store import box
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

_LOAD: Final = text(
    """
    SELECT attempt.id, attempt.org_id, attempt.user_id, attempt.status, attempt.sealed_code,
           attempt.expires_at, app.id AS connection_id, app.github_client_id,
           app.sealed_client_secret
    FROM github_link_attempts AS attempt
    LEFT JOIN connections AS app
      ON app.org_id = attempt.org_id AND app.kind = 'github_fix' AND app.state <> 'disconnected'
    WHERE attempt.org_id = :org_id AND attempt.id = :attempt_id
    """
)
_LINKED: Final = text(
    """
    INSERT INTO github_user_links (user_id, org_id, github_user_id, github_login, linked_at)
    VALUES (:user_id, :org_id, :github_user_id, :github_login, :at)
    ON CONFLICT (user_id) DO UPDATE SET
        github_user_id = EXCLUDED.github_user_id, github_login = EXCLUDED.github_login,
        linked_at = EXCLUDED.linked_at
    WHERE github_user_links.org_id = :org_id
    """
)
_FINISHED: Final = text(
    """
    UPDATE github_link_attempts SET status = :status, reason = :reason, sealed_code = NULL
    WHERE org_id = :org_id AND id = :attempt_id AND status = 'running'
    RETURNING id
    """
)


async def complete_github_user_link(
    engine: AsyncEngine,
    *,
    org_id: str,
    attempt_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> str | None:
    """Returns the attempt's outcome, or None when there was nothing to do."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(_LOAD, {"org_id": org_id, "attempt_id": attempt_id})
        ).one_or_none()
    if row is None or row.status != "running" or row.sealed_code is None:
        return None
    if row.expires_at <= now:
        return await _finish(engine, row, LINK_FAILED, "expired")
    if (
        row.connection_id is None
        or row.github_client_id is None
        or row.sealed_client_secret is None
    ):
        return await _finish(engine, row, LINK_FAILED, "key_unreadable")
    try:
        code = box(keys, GITHUB_LINK_CODE_PURPOSE).open(
            bytes(row.sealed_code),
            owner=github_link_code_owner(str(row.org_id), str(row.user_id), str(row.id)),
        )
        client_secret = box(keys, FIX_APP_CLIENT_SECRET_PURPOSE).open(
            bytes(row.sealed_client_secret),
            owner=connection_secret_owner(str(row.org_id), str(row.connection_id)),
        )
    except (SealError, ValueError):
        return await _finish(engine, row, LINK_FAILED, "key_unreadable")
    try:
        token = exchange_code(
            endpoints, client_id=row.github_client_id, client_secret=client_secret, code=code
        )
        person = user_of_token(endpoints, token)
    except GitHubFailure as failure:
        logger.warning("a GitHub account link failed", attempt_id=attempt_id, failure=failure.code)
        if failure.passing:
            return await _finish(engine, row, LINK_FAILED, "github_unavailable")
        return await _finish(engine, row, LINK_REFUSED, "refused_by_github")
    del token
    return await _finish(
        engine, row, LINK_LINKED, None, person_id=person.user_id, login=person.login, at=now
    )


async def _finish(
    engine: AsyncEngine,
    row: Any,
    status: str,
    reason: str | None,
    *,
    person_id: int | None = None,
    login: str | None = None,
    at: datetime | None = None,
) -> str | None:
    ids = {"org_id": row.org_id, "attempt_id": row.id}
    async with engine.begin() as connection:
        finished = (
            await connection.execute(_FINISHED, {**ids, "status": status, "reason": reason})
        ).one_or_none()
        if finished is None:
            await connection.rollback()
            return None
        if status == LINK_LINKED:
            await connection.execute(
                _LINKED,
                {
                    "org_id": row.org_id,
                    "user_id": row.user_id,
                    "github_user_id": person_id,
                    "github_login": login,
                    "at": at,
                },
            )
            await connection.execute(
                text(RECORD_CASE_AUDIT_SQL),
                case_audit_parameters(
                    org_id=row.org_id,
                    actor_user_id=row.user_id,
                    action=GITHUB_ACCOUNT_LINKED,
                    target=f"user:{row.user_id}",
                ),
            )
    return status


@task(COMPLETE_GITHUB_USER_LINK_TASK)
def complete_github_user_link_task(*, attempt_id: str, org_id: str) -> None:
    with_engine(
        lambda engine: complete_github_user_link(
            engine,
            org_id=org_id,
            attempt_id=attempt_id,
            keys=get_settings().connection_secret_keys,
            endpoints=fix_endpoints(),
            now=datetime.now(UTC),
        )
    )
