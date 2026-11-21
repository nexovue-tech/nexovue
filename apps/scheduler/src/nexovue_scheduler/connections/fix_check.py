"""A person's check run (F5 design, *The check run*; plan T4.12, T5.1).

`connections.check_fix_against_current_code` runs on `scheduler-connections`, which holds
the connection key and never the AI key: nothing here calls a provider.

1. The check row must be `running`, in the job's own organization; the person must still be
   in it with a role that may ask; and a consent covering the row's folder must not be
   withdrawn. **A consent that ended by itself does not stop a check:** nothing is sent.
2. A `contents: read` token for this one repository.
3. Under the owner's requirement (D20), the person's stored GitHub id gives their current
   login, and GitHub says their access: at least `read` passes. Anything else, an error
   included, is `refused` with its code, and audited. **The suggestion's row never moves.**

`read` (**Check my access**) ends there, and lets this person read quoted code for an hour.
`patch` goes on to re-read the files and rebuild the patch (`fix_patch.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.fixes.records import (
    CHECK_FAILED,
    CHECK_FIX_TASK,
    CHECK_FOR_PATCH,
    CHECK_FOR_READ,
    CHECK_RUNNING,
    CHECK_UNCHANGED,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import check_rows, fix_patch
from nexovue_scheduler.connections.fix_github import FixEndpoints, GitHubFailure
from nexovue_scheduler.connections.source_pack import (
    Ending,
    check_access,
    endpoints,
    fix_client,
    load_fix_row,
)
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

_PURPOSES: Final = (CHECK_FOR_READ, CHECK_FOR_PATCH)


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
    check = await check_rows.load_check(engine, org_id, check_id)
    if check is None or check.status != CHECK_RUNNING or check.purpose not in _PURPOSES:
        return None
    row = await load_fix_row(engine, org_id, str(check.fix_id))
    if row is None or row.kind != "source":
        return await check_rows.finish(
            engine, org_id, check, CHECK_FAILED, "nexovue_error", now=now
        )
    stopped = await check_rows.why_stopped(engine, check, row)
    if stopped is not None:
        return await check_rows.finish(engine, org_id, check, CHECK_FAILED, stopped, now=now)
    if check.purpose == CHECK_FOR_PATCH:
        return await fix_patch.check_patch(
            engine, check, row, keys=keys, endpoints=endpoints, now=now
        )
    try:
        _ask_github(row, await check_rows.github_user_id(engine, org_id, check), keys, endpoints)
    except Ending as ending:
        return await check_rows.end_on_refusal(engine, org_id, check, ending, now=now)
    except GitHubFailure as failure:
        return await check_rows.end_on_github_failure(engine, org_id, check, failure, now=now)
    return await check_rows.finish(engine, org_id, check, CHECK_UNCHANGED, None, now=now)


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
