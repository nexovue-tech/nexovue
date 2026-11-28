"""A check run's own row: reading it, the person and consent it still needs, and ending it
(F5 design, *The check run*, steps 1 and 3; plan T4.12, T5.1).

Shared by both purposes the check job runs, `read` (**Check my access**) and `patch`.
Every statement binds the job's own `org_id`; every end is conditional on the check still
`running`, so a repeat finds nothing to do. A refusal or failure is audited, naming the
person and the code, never a GitHub login (AC31).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.records import (
    CHECK_FAILED,
    CHECK_FOR_READ,
    CHECK_REFUSED,
    FIX_CHECK_REFUSED,
    PACK_MAX_ATTEMPTS,
    PATCH_CHECK_LIFETIME,
    READ_CHECK_LIFETIME,
    ROLES_WHO_ASK,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.connections.fix_github import GitHubFailure
from nexovue_scheduler.connections.source_pack import RETRY_SECONDS, Ending
from nexovue_scheduler.jobs.registry import Retry, attempt

logger = get_logger(__name__)

# What a check's failures store (design, *The check run*, step 3).
CHECK_REASON_OF: Final = {
    "not_linked": "not_linked",
    "no_github_access": "no_github_access",
    "access_unknown": "access_unknown",
    "permission_missing": "permission_missing",
}
REFUSALS: Final = frozenset({"not_linked", "no_github_access", "access_unknown"})
# Ends that are audited: a refusal or a failure, never a pass or "out of date".
_AUDITED: Final = frozenset({CHECK_REFUSED, CHECK_FAILED})

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


async def load_check(engine: AsyncEngine, org_id: str, check_id: str) -> Any | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(_CHECK, {"org_id": org_id, "check_id": check_id})
        ).one_or_none()


def lifetime_of(purpose: str) -> timedelta:
    """A finished `read` check lasts an hour (D20); a `patch` check 15 minutes (design)."""
    return READ_CHECK_LIFETIME if purpose == CHECK_FOR_READ else PATCH_CHECK_LIFETIME


async def why_stopped(engine: AsyncEngine, check: Any, row: Any) -> str | None:
    """Step 1: the person still in the organization with a role that may ask, and a
    consent covering the folder not withdrawn. **A consent that ended by itself does not
    stop a check:** nothing goes to a provider here."""
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


async def github_user_id(engine: AsyncEngine, org_id: str, check: Any) -> int | None:
    async with engine.connect() as connection:
        found = await connection.scalar(_LINK, {"org_id": org_id, "user_id": check.requested_by})
    return None if found is None else int(found)


async def finish(
    engine: AsyncEngine,
    org_id: str,
    check: Any,
    status: str,
    reason: str | None,
    *,
    now: datetime,
) -> str | None:
    """The check's end, only while it is still `running`."""
    async with engine.begin() as connection:
        return await finish_in(connection, org_id, check, status, reason, now=now)


async def finish_in(
    connection: AsyncConnection,
    org_id: str,
    check: Any,
    status: str,
    reason: str | None,
    *,
    now: datetime,
) -> str | None:
    finished = (
        await connection.execute(
            _FINISH,
            {
                "org_id": org_id,
                "check_id": check.id,
                "status": status,
                "reason": reason,
                "at": now,
                "expires_at": now + lifetime_of(check.purpose),
            },
        )
    ).one_or_none()
    if finished is None:
        return None
    if status in _AUDITED:
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
                    "purpose": check.purpose,
                    "reason": reason,
                },
            ),
        )
    logger.info(
        "a check run ended",
        check_id=str(check.id),
        purpose=check.purpose,
        status=status,
        reason=reason,
    )
    return status


async def end_on_refusal(
    engine: AsyncEngine, org_id: str, check: Any, ending: Ending, *, now: datetime
) -> str | None:
    """Step 3's refusals, and an App that cannot be used, by their codes."""
    reason = CHECK_REASON_OF.get(ending.reason, "nexovue_error")
    status = CHECK_REFUSED if reason in REFUSALS else CHECK_FAILED
    return await finish(engine, org_id, check, status, reason, now=now)


async def end_on_github_failure(
    engine: AsyncEngine, org_id: str, check: Any, failure: GitHubFailure, *, now: datetime
) -> str | None:
    """A passing failure is retried while attempts last; then `github_unavailable`."""
    logger.warning(
        "a check run's GitHub call failed",
        check_id=str(check.id),
        failure=failure.code,
        status=failure.status,
    )
    if failure.passing and attempt() < PACK_MAX_ATTEMPTS:
        raise Retry(RETRY_SECONDS, failure.code) from failure
    reason = "permission_missing" if failure.code == "permission_missing" else "github_unavailable"
    return await finish(engine, org_id, check, CHECK_FAILED, reason, now=now)
