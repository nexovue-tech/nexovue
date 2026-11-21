"""A person's check run, for `read`: **Check my access** (F5 design, *The check run*, steps 1
to 3; spec D20, AC10a's read half; plan T4.12). T5.1 adds `patch` to this job.

`connections.check_fix_against_current_code` runs on `scheduler-connections`, which holds
the connection key and never the AI key: nothing here calls a provider or reads a file.

1. The check row must be `running`, in the job's own organization; the person must still be
   in it with a role that may ask; and a consent covering the row's folder must not be
   withdrawn. **A consent that ended by itself does not stop a check:** nothing is sent.
2. A `contents: read` token for this one repository.
3. The person's stored GitHub id gives their current login, and GitHub says their access.
   At least `read` passes, and the row then lets this person read quoted code for an hour
   (D20's guess). Anything else, an error included, is `refused` with its code, and
   audited. **The suggestion's row never moves.**

Every write is conditional on the check still `running`, so a repeat finds nothing to do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.records import (
    CHECK_FAILED,
    CHECK_FIX_TASK,
    CHECK_FOR_READ,
    CHECK_REFUSED,
    CHECK_RUNNING,
    CHECK_UNCHANGED,
    FIX_CHECK_REFUSED,
    PACK_MAX_ATTEMPTS,
    READ_CHECK_LIFETIME,
    ROLES_WHO_ASK,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections.fix_github import FixEndpoints, GitHubFailure
from nexovue_scheduler.connections.source_pack import (
    RETRY_SECONDS,
    Ending,
    check_access,
    endpoints,
    fix_client,
    load_fix_row,
)
from nexovue_scheduler.jobs.registry import Retry, attempt, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# What a check's failures store (design, *The check run*, step 3).
_CHECK_REASON_OF: Final = {
    "not_linked": "not_linked",
    "no_github_access": "no_github_access",
    "access_unknown": "access_unknown",
    "permission_missing": "permission_missing",
}
_REFUSALS: Final = frozenset({"not_linked", "no_github_access", "access_unknown"})

# Every statement binds the job's own `org_id`.
_CHECK: Final = text(
    """
    SELECT id, fix_id, requested_by, purpose, status FROM finding_fix_checks
    WHERE org_id = :org_id AND id = :check_id
    """
)
_PERSON: Final = text(
    """
    SELECT id FROM users
    WHERE org_id = :org_id AND id = :user_id AND removed_at IS NULL
      AND role::text = ANY(CAST(:roles AS text[]))
    """
)
_LINK: Final = text(
    "SELECT github_user_id FROM github_user_links WHERE org_id = :org_id AND user_id = :user_id"
)
_UNWITHDRAWN: Final = text(
    """
    SELECT folder_scope FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    """
)
_FINISH: Final = text(
    """
    UPDATE finding_fix_checks
    SET status = :status, reason = :reason, finished_at = :at, expires_at = :expires_at
    WHERE org_id = :org_id AND id = :check_id AND status = 'running'
    RETURNING fix_id
    """
)
_FINDING_OF: Final = text(
    "SELECT finding_id FROM finding_fixes WHERE org_id = :org_id AND id = :fix_id"
)


async def check_fix(
    engine: AsyncEngine,
    *,
    org_id: str,
    check_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> str | None:
    """One check run. Returns the status it left, or None when it found nothing to do."""
    ids = {"org_id": org_id, "check_id": check_id}
    async with engine.connect() as connection:
        check = (await connection.execute(_CHECK, ids)).one_or_none()
    if check is None or check.status != CHECK_RUNNING or check.purpose != CHECK_FOR_READ:
        return None
    row = await load_fix_row(engine, org_id, str(check.fix_id))
    if row is None or row.kind != "source":
        return await _finish(engine, org_id, check, CHECK_FAILED, "nexovue_error", now=now)
    stopped = await _why_stopped(engine, check, row)
    if stopped is not None:
        return await _finish(engine, org_id, check, CHECK_FAILED, stopped, now=now)
    try:
        _ask_github(row, await _github_user_id(engine, org_id, check), keys, endpoints)
    except Ending as ending:
        reason = _CHECK_REASON_OF.get(ending.reason, "nexovue_error")
        status = CHECK_REFUSED if reason in _REFUSALS else CHECK_FAILED
        return await _finish(engine, org_id, check, status, reason, now=now)
    except GitHubFailure as failure:
        logger.warning(
            "a check run's GitHub call failed",
            check_id=check_id,
            failure=failure.code,
            status=failure.status,
        )
        if failure.passing and attempt() < PACK_MAX_ATTEMPTS:
            raise Retry(RETRY_SECONDS, failure.code) from failure
        reason = (
            "permission_missing" if failure.code == "permission_missing" else "github_unavailable"
        )
        return await _finish(engine, org_id, check, CHECK_FAILED, reason, now=now)
    return await _finish(engine, org_id, check, CHECK_UNCHANGED, None, now=now)


async def _why_stopped(engine: AsyncEngine, check: Any, row: Any) -> str | None:
    """Step 1: the person still in the organization with a role that may ask, and a
    consent covering the folder not withdrawn."""
    ids = {"org_id": row.org_id}
    async with engine.connect() as connection:
        person = await connection.scalar(
            _PERSON, {**ids, "user_id": check.requested_by, "roles": list(ROLES_WHO_ASK)}
        )
        scopes = (
            []
            if row.connection_repository_id is None
            else (
                await connection.execute(
                    _UNWITHDRAWN, {**ids, "repository_id": row.connection_repository_id}
                )
            ).all()
        )
    if person is None:
        return "access_unknown"
    if not any(folder_covered(scope.folder_scope, row.folder) for scope in scopes):
        return "consent_withdrawn"
    return None


async def _github_user_id(engine: AsyncEngine, org_id: str, check: Any) -> int | None:
    async with engine.connect() as connection:
        found = await connection.scalar(_LINK, {"org_id": org_id, "user_id": check.requested_by})
    return None if found is None else int(found)


def _ask_github(
    row: Any, github_user_id: int | None, keys: ConfiguredKeys, endpoints: FixEndpoints
) -> None:
    """Steps 2 and 3, with a read-only token revoked whatever happens."""
    github = fix_client(row, keys, endpoints)
    token = github.read_token(row.github_installation_id, int(row.external_id))
    try:
        check_access(github, token, row.key, github_user_id)
    finally:
        github.revoke(token)


async def _finish(
    engine: AsyncEngine,
    org_id: str,
    check: Any,
    status: str,
    reason: str | None,
    *,
    now: datetime,
) -> str | None:
    """The check's result, only while it is still `running`; a refusal is audited, naming
    the person and the code, never a GitHub login (AC31)."""
    expires_at = now + READ_CHECK_LIFETIME
    async with engine.begin() as connection:
        finished = (
            await connection.execute(
                _FINISH,
                {
                    "org_id": org_id,
                    "check_id": check.id,
                    "status": status,
                    "reason": reason,
                    "at": now,
                    "expires_at": expires_at,
                },
            )
        ).one_or_none()
        if finished is None:
            return None
        if status != CHECK_UNCHANGED:
            finding_id = await connection.scalar(
                _FINDING_OF, {"org_id": org_id, "fix_id": finished.fix_id}
            )
            await connection.execute(
                text(RECORD_CASE_AUDIT_SQL),
                case_audit_parameters(
                    org_id=org_id,
                    actor_user_id=check.requested_by,
                    action=FIX_CHECK_REFUSED,
                    target=f"{FINDING_AUDIT_TARGET}:{finding_id}",
                    detail={
                        "fix_id": str(finished.fix_id),
                        "check_id": str(check.id),
                        "purpose": CHECK_FOR_READ,
                        "reason": reason,
                    },
                ),
            )
    logger.info("a check run ended", check_id=str(check.id), status=status, reason=reason)
    return status


@task(CHECK_FIX_TASK)
def check_fix_task(*, check_id: str, org_id: str) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: check_fix(
            engine,
            org_id=org_id,
            check_id=check_id,
            keys=settings.connection_secret_keys,
            endpoints=endpoints(),
            now=datetime.now(UTC),
        )
    )
    logger.info("a check run job ended", check_id=check_id, org_id=org_id, did=did)
