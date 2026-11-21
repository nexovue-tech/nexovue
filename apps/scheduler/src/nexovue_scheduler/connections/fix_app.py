"""F5's Fix App jobs on `scheduler-connections` (design, *Fix App*; plan T3.3; ADR-082).

- `connections.convert_fix_app`: opens the sealed manifest code, trades it once for the
  App's key and OAuth client id and secret, and seals the key and the secret in the same
  transaction that drops the code. F22's conversion discards the client secret; the account
  link's exchange needs it, so F5 keeps it (design: stored at creation, not at Step 7).
- `connections.refresh_fix_app`: binds the App's own installation, reads the repositories
  it reaches (F22's `write_reach`, so `is_reachable` follows GitHub) and the permissions
  GitHub granted, so eligibility reads `granted_permissions` with no call on a page view.

Ids only, every statement bound to the job's organization; no secret in a log line.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.checks.sealing import ConfiguredKeys, SealError
from nexovue_platform.connections import (
    CONNECTION_CREDENTIAL_PURPOSE,
    CONNECTION_MANIFEST_CODE_PURPOSE,
    connection_secret_owner,
)
from nexovue_platform.fixes.records import (
    CONVERT_FIX_APP_TASK,
    FIX_APP_CLIENT_SECRET_PURPOSE,
    REFRESH_FIX_APP_TASK,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import github, states
from nexovue_scheduler.connections.fix_github import (
    FixEndpoints,
    FixGitHub,
    GitHubFailure,
    convert_manifest,
)
from nexovue_scheduler.connections.store import box, write_standing
from nexovue_scheduler.connections.tasks import write_reach
from nexovue_scheduler.connections.trackers import TrackerFailure
from nexovue_scheduler.jobs.registry import Retry, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

CONVERSION_RETRY_SECONDS: Final = 30

_LOAD: Final = text(
    """
    SELECT id, org_id, state, sealed_credential, sealed_manifest_code, setup_expires_at,
           github_app_id, github_installation_id, consecutive_failures, failing_since,
           last_failure, last_failure_status
    FROM connections
    WHERE org_id = :org_id AND id = :connection_id AND kind = 'github_fix'
      AND state <> 'disconnected'
    """
)
_STORE_APP: Final = text(
    """
    UPDATE connections
    SET sealed_credential = :credential, sealed_client_secret = :client_secret,
        github_client_id = :client_id, sealed_manifest_code = NULL, github_app_id = :app_id,
        github_app_slug = :app_slug, last_failure = NULL, last_failure_status = NULL
    WHERE org_id = :org_id AND id = :connection_id AND kind = 'github_fix'
      AND sealed_manifest_code IS NOT NULL AND state = 'setting_up'
    """
)
_DROP_CODE: Final = text(
    """
    UPDATE connections SET sealed_manifest_code = NULL, last_failure = :failure
    WHERE org_id = :org_id AND id = :connection_id AND kind = 'github_fix'
      AND sealed_manifest_code IS NOT NULL
    """
)
_GRANTED: Final = text(
    """
    UPDATE connections SET granted_permissions = CAST(:granted AS jsonb), granted_at = :at
    WHERE org_id = :org_id AND id = :connection_id AND kind = 'github_fix'
      AND state <> 'disconnected'
    """
)


async def _load(engine: AsyncEngine, org_id: str, connection_id: str) -> Any | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(_LOAD, {"org_id": org_id, "connection_id": connection_id})
        ).one_or_none()


async def convert_fix_app(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> None:
    row = await _load(engine, org_id, connection_id)
    if row is None or row.sealed_manifest_code is None:
        return
    ids = {"org_id": org_id, "connection_id": connection_id}
    if row.setup_expires_at is None or row.setup_expires_at <= now:
        await _drop_code(engine, ids, "manifest_expired")
        return
    owner = connection_secret_owner(org_id, connection_id)
    try:
        code = box(keys, CONNECTION_MANIFEST_CODE_PURPOSE).open(
            bytes(row.sealed_manifest_code), owner=owner
        )
    except (SealError, ValueError):
        await _drop_code(engine, ids, "key_unreadable")
        return
    try:
        converted = convert_manifest(endpoints, code)
    except GitHubFailure as failure:
        logger.warning(
            "GitHub did not convert a Fix App code",
            connection_id=connection_id,
            failure=failure.code,
        )
        if failure.passing:
            raise Retry(CONVERSION_RETRY_SECONDS, failure.code) from failure
        await _drop_code(engine, ids, "manifest_refused")
        return
    async with engine.begin() as connection:
        await connection.execute(
            _STORE_APP,
            {
                **ids,
                "credential": box(keys, CONNECTION_CREDENTIAL_PURPOSE).seal(
                    converted.private_key, owner=owner
                ),
                "client_secret": box(keys, FIX_APP_CLIENT_SECRET_PURPOSE).seal(
                    converted.client_secret, owner=owner
                ),
                "client_id": converted.client_id,
                "app_id": converted.app_id,
                "app_slug": converted.app_slug,
            },
        )
    logger.info("the Nexovue suggestions App was created", connection_id=connection_id)


async def _drop_code(engine: AsyncEngine, ids: dict[str, str], failure: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(_DROP_CODE, {**ids, "failure": failure})


async def refresh_fix_app(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> None:
    row = await _load(engine, org_id, connection_id)
    if row is None or row.sealed_credential is None or row.github_app_id is None:
        return
    standing = states.Standing(
        state=row.state,
        consecutive_failures=row.consecutive_failures,
        failing_since=row.failing_since,
        last_failure=row.last_failure,
        last_failure_status=row.last_failure_status,
    )
    try:
        key = box(keys, CONNECTION_CREDENTIAL_PURPOSE).open(
            bytes(row.sealed_credential), owner=connection_secret_owner(org_id, connection_id)
        )
        app = github.GitHubApp(
            endpoints.tracker_endpoint(), app_id=row.github_app_id, private_key=key
        )
        reach = github.GitHubTracker(app, installation_id=None).reach()
        granted = FixGitHub(
            endpoints, app_id=row.github_app_id, private_key=key
        ).granted_permissions(reach.github_installation_id or 0)
    except (SealError, ValueError):
        failed = TrackerFailure("key_unreadable")
        await write_standing(
            engine,
            org_id,
            connection_id,
            states.after_failure(standing, failed.failure, None, now=now),
        )
        return
    except TrackerFailure as failure:
        await write_standing(
            engine,
            org_id,
            connection_id,
            states.after_failure(standing, failure.failure, failure.status, now=now),
        )
        return
    except GitHubFailure as failure:
        word = "unauthorized" if failure.code == "permission_missing" else "server_error"
        await write_standing(
            engine,
            org_id,
            connection_id,
            states.after_failure(standing, word, failure.status, now=now),
        )
        return
    await write_reach(engine, org_id, connection_id, reach, now=now)
    async with engine.begin() as connection:
        await connection.execute(
            _GRANTED,
            {
                "org_id": org_id,
                "connection_id": connection_id,
                "granted": json.dumps(granted),
                "at": now,
            },
        )
    await write_standing(engine, org_id, connection_id, states.after_success(standing, now=now))


def _keys() -> ConfiguredKeys:
    return get_settings().connection_secret_keys


def _endpoints() -> FixEndpoints:
    return FixEndpoints(private_allowed=get_settings().private_probe_targets_allowed)


@task(CONVERT_FIX_APP_TASK)
def convert_fix_app_task(*, org_id: str, connection_id: str) -> None:
    with_engine(
        lambda engine: convert_fix_app(
            engine,
            org_id=org_id,
            connection_id=connection_id,
            keys=_keys(),
            endpoints=_endpoints(),
            now=datetime.now(UTC),
        )
    )


@task(REFRESH_FIX_APP_TASK)
def refresh_fix_app_task(*, org_id: str, connection_id: str) -> None:
    with_engine(
        lambda engine: refresh_fix_app(
            engine,
            org_id=org_id,
            connection_id=connection_id,
            keys=_keys(),
            endpoints=_endpoints(),
            now=datetime.now(UTC),
        )
    )
