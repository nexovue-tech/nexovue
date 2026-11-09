"""The connection jobs (F22 plan T3.6): converting a GitHub manifest code, refreshing what
a connection reaches, and the connection test (FR30).

**Kind-agnostic.** A job loads its connection, opens what it needs, asks the adapter for
its kind (`trackers.tracker_for`), and writes what it learned through one state machine
(`states.py`). Only the manifest conversion is GitHub's own, because the manifest flow
is. Jira later is an adapter, not a new job.

**Ids only, and the job's own organization on every read** (AC74, AC129): each job takes
`org_id` and `connection_id`, and every statement binds both, so a job naming another
organization's connection finds no row and stops before any call. A refresh takes no
installation id: the bound installation is the one GitHub lists for the App (AC38).

**No secret in a log line or a job row.** The code, the App key and the webhook secret
are opened in memory and sealed again before they touch Postgres; failures are logged as
an enum and a status. No lock is held across a call out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.connections import (
    CHECK_CONNECTION_TASK,
    CONNECTION_CREDENTIAL_PURPOSE,
    CONNECTION_MANIFEST_CODE_PURPOSE,
    CONNECTION_TEST_RESULT_KIND,
    CONNECTION_TEST_RESULT_TTL_SECONDS,
    CONNECTION_WEBHOOK_SECRET_PURPOSE,
    CONVERT_MANIFEST_TASK,
    GITHUB,
    REFRESH_CONNECTION_TASK,
    TRACKER_KINDS,
    connection_secret_owner,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import github, states, trackers
from nexovue_scheduler.connections.trackers import Reach, StoredConnection, TrackerFailure
from nexovue_scheduler.jobs import results
from nexovue_scheduler.jobs.registry import Retry, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

__all__ = ["Retry", "check", "convert_manifest", "refresh"]

logger = get_logger(__name__)

# A conversion GitHub could not answer is tried again by its job, well inside the code's hour.
CONVERSION_RETRY_SECONDS: Final = 30
_PASSING_CONVERSION_FAILURES: Final = ("unreachable", "timed_out", "server_error", "rate_limited")

# ------------------------------------------------------------------- statements
# Every one binds `org_id` and the connection's id (AC129).

_LOAD: Final = text(
    """
    SELECT id, org_id, kind, state, sealed_credential, sealed_manifest_code, setup_expires_at,
           github_app_id, github_installation_id, consecutive_failures, failing_since,
           last_failure, last_failure_status
    FROM connections
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
_DROP_CODE: Final = text(
    """
    UPDATE connections SET sealed_manifest_code = NULL, last_failure = :failure,
                           last_failure_status = :status
    WHERE org_id = :org_id AND id = :connection_id AND sealed_manifest_code IS NOT NULL
    """
)
_STORE_APP: Final = text(
    """
    UPDATE connections
    SET sealed_credential = :credential, sealed_webhook_secret = :webhook_secret,
        sealed_manifest_code = NULL, github_app_id = :app_id, github_app_slug = :app_slug,
        last_failure = NULL, last_failure_status = NULL
    WHERE org_id = :org_id AND id = :connection_id AND sealed_manifest_code IS NOT NULL
      AND state = 'setting_up'
    """
)
_STAND: Final = text(
    """
    UPDATE connections
    SET state = :state, consecutive_failures = :consecutive_failures,
        failing_since = :failing_since, last_failure = :last_failure,
        last_failure_status = :last_failure_status,
        last_ok_at = COALESCE(:last_ok_at, last_ok_at)
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
_BIND: Final = text(
    """
    UPDATE connections SET github_installation_id = :installation_id, repositories_read_at = :at
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
_UPSERT_PLACE: Final = text(
    """
    INSERT INTO connection_repositories
        (org_id, connection_id, external_id, key, is_private, is_reachable)
    VALUES (:org_id, :connection_id, :external_id, :key, :is_private, true)
    ON CONFLICT (connection_id, external_id)
    DO UPDATE SET key = EXCLUDED.key, is_private = EXCLUDED.is_private, is_reachable = true
    """
)
_UNREACHED_PLACES: Final = text(
    """
    UPDATE connection_repositories SET is_reachable = false
    WHERE org_id = :org_id AND connection_id = :connection_id
      AND NOT (external_id = ANY(CAST(:reached AS text[])))
    """
)


@dataclass(frozen=True, slots=True)
class _Loaded:
    row: Any

    @property
    def standing(self) -> states.Standing:
        return states.Standing(
            state=self.row.state,
            consecutive_failures=self.row.consecutive_failures,
            failing_since=self.row.failing_since,
            last_failure=self.row.last_failure,
            last_failure_status=self.row.last_failure_status,
        )


async def _load(engine: AsyncEngine, org_id: str, connection_id: str) -> _Loaded | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(_LOAD, {"org_id": org_id, "connection_id": connection_id})
        ).one_or_none()
    if row is None or row.kind not in TRACKER_KINDS:
        logger.info("a connection job found no live connection", connection_id=connection_id)
        return None
    return _Loaded(row)


def _box(keys: ConfiguredKeys, purpose: str) -> SealedBox:
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose)


async def _stand(
    engine: AsyncEngine, org_id: str, connection_id: str, standing: states.Standing
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            _STAND,
            {
                "org_id": org_id,
                "connection_id": connection_id,
                "state": standing.state,
                "consecutive_failures": standing.consecutive_failures,
                "failing_since": standing.failing_since,
                "last_failure": standing.last_failure,
                "last_failure_status": standing.last_failure_status,
                "last_ok_at": standing.last_ok_at,
            },
        )


async def _failed(
    engine: AsyncEngine,
    loaded: _Loaded,
    failure: TrackerFailure,
    *,
    now: datetime,
) -> states.Standing:
    logger.warning(
        "a connection call failed",
        connection_id=str(loaded.row.id),
        failure=failure.failure,
        status=failure.status,
    )
    standing = states.after_failure(loaded.standing, failure.failure, failure.status, now=now)
    await _stand(engine, str(loaded.row.org_id), str(loaded.row.id), standing)
    return standing


def _stored(loaded: _Loaded, keys: ConfiguredKeys) -> StoredConnection:
    """The row's facts and its opened credential; `TrackerFailure` if it will not open."""
    row = loaded.row
    if row.sealed_credential is None:
        raise TrackerFailure("app_not_installed")
    try:
        credential = _box(keys, CONNECTION_CREDENTIAL_PURPOSE).open(
            bytes(row.sealed_credential),
            owner=connection_secret_owner(str(row.org_id), str(row.id)),
        )
    except (SealError, ValueError) as unreadable:
        raise TrackerFailure("key_unreadable") from unreadable
    return StoredConnection(
        kind=row.kind,
        credential=credential,
        github_app_id=row.github_app_id,
        github_installation_id=row.github_installation_id,
    )


def _tracker(
    loaded: _Loaded, keys: ConfiguredKeys, endpoint: github.GitHubEndpoint | None
) -> trackers.Tracker:
    stored = _stored(loaded, keys)
    if endpoint is None:
        return trackers.tracker_for(stored, private_allowed=_private_allowed())
    return github.GitHubTracker.for_connection(stored, endpoint)


# ------------------------------------------------------------ the manifest code


async def convert_manifest(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    endpoint: github.GitHubEndpoint,
    now: datetime,
) -> None:
    """Open the code, trade it for the App's key and webhook secret, seal both, and null
    the code in that one transaction (design, *Connecting GitHub*, step 4)."""
    loaded = await _load(engine, org_id, connection_id)
    row = None if loaded is None else loaded.row
    if row is None or row.kind != GITHUB or row.sealed_manifest_code is None:
        return
    ids = {"org_id": org_id, "connection_id": connection_id}
    if row.setup_expires_at is None or row.setup_expires_at <= now:
        await _drop_code(engine, ids, "manifest_expired", None)
        return
    owner = connection_secret_owner(org_id, connection_id)
    try:
        code = _box(keys, CONNECTION_MANIFEST_CODE_PURPOSE).open(
            bytes(row.sealed_manifest_code), owner=owner
        )
    except (SealError, ValueError):
        await _drop_code(engine, ids, "key_unreadable", None)
        return
    try:
        converted = github.convert_manifest(endpoint, code)
    except TrackerFailure as failure:
        logger.warning(
            "GitHub did not convert a manifest code",
            connection_id=connection_id,
            failure=failure.failure,
            status=failure.status,
        )
        if failure.failure in _PASSING_CONVERSION_FAILURES:
            raise Retry(CONVERSION_RETRY_SECONDS, failure.failure) from failure
        await _drop_code(engine, ids, "manifest_refused", failure.status)
        return
    webhook = converted.webhook_secret
    async with engine.begin() as connection:
        await connection.execute(
            _STORE_APP,
            {
                **ids,
                "credential": _box(keys, CONNECTION_CREDENTIAL_PURPOSE).seal(
                    converted.private_key, owner=owner
                ),
                "webhook_secret": None
                if webhook is None
                else _box(keys, CONNECTION_WEBHOOK_SECRET_PURPOSE).seal(webhook, owner=owner),
                "app_id": converted.app_id,
                "app_slug": converted.app_slug,
            },
        )
    logger.info("a GitHub App was created for a connection", connection_id=connection_id)


async def _drop_code(
    engine: AsyncEngine, ids: dict[str, str], failure: str, status: int | None
) -> None:
    async with engine.begin() as connection:
        await connection.execute(_DROP_CODE, {**ids, "failure": failure, "status": status})
    logger.warning(
        "a manifest code was dropped", connection_id=ids["connection_id"], failure=failure
    )


# ------------------------------------------------------------------ the refresh


async def refresh(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    endpoint: github.GitHubEndpoint | None = None,
    now: datetime,
) -> None:
    """Bind the connection's own installation and read every place it reaches. A place
    no longer listed stays, unreachable, so its links can say so (design, row 10)."""
    loaded = await _load(engine, org_id, connection_id)
    if loaded is None:
        return
    try:
        reach = _tracker(loaded, keys, endpoint).reach()
    except TrackerFailure as failure:
        await _failed(engine, loaded, failure, now=now)
        return
    await _write_reach(engine, org_id, connection_id, reach, now=now)
    if not reach.places:
        await _failed(engine, loaded, TrackerFailure("no_repository"), now=now)
        return
    await _stand(engine, org_id, connection_id, states.after_success(loaded.standing, now=now))


async def _write_reach(
    engine: AsyncEngine, org_id: str, connection_id: str, reach: Reach, *, now: datetime
) -> None:
    ids = {"org_id": org_id, "connection_id": connection_id}
    async with engine.begin() as connection:
        await connection.execute(
            _BIND, {**ids, "installation_id": reach.github_installation_id, "at": now}
        )
        for place in reach.places:
            await connection.execute(
                _UPSERT_PLACE,
                {
                    **ids,
                    "external_id": place.external_id,
                    "key": place.key,
                    "is_private": place.is_private,
                },
            )
        await connection.execute(
            _UNREACHED_PLACES, {**ids, "reached": [place.external_id for place in reach.places]}
        )


# -------------------------------------------------------------------- the test


async def check(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    endpoint: github.GitHubEndpoint | None = None,
    now: datetime,
    request_id: str | None = None,
) -> None:
    """Prove the stored credential works, move the state, and leave the result for the
    person who asked, under their organization (FR30)."""
    loaded = await _load(engine, org_id, connection_id)
    if loaded is None:
        return
    try:
        _tracker(loaded, keys, endpoint).check()
    except TrackerFailure as failure:
        standing = await _failed(engine, loaded, failure, now=now)
        outcome: dict[str, Any] = {"ok": False, "failure": failure.failure}
    else:
        standing = states.after_success(loaded.standing, now=now)
        await _stand(engine, org_id, connection_id, standing)
        outcome = {"ok": True, "failure": None}
    if request_id is not None:
        await results.store(
            engine,
            kind=CONNECTION_TEST_RESULT_KIND,
            org_id=org_id,
            request_id=request_id,
            payload={**outcome, "state": standing.state},
            ttl_seconds=CONNECTION_TEST_RESULT_TTL_SECONDS,
        )


# ------------------------------------------------------- the registered tasks


def _private_allowed() -> bool:
    return get_settings().private_probe_targets_allowed


def _keys() -> ConfiguredKeys:
    return get_settings().connection_secret_keys


def _github() -> github.GitHubEndpoint:
    return github.GitHubEndpoint(base_url=github.GITHUB_API, private_allowed=_private_allowed())


@task(CONVERT_MANIFEST_TASK)
def convert_manifest_task(*, org_id: str, connection_id: str) -> None:
    with_engine(
        lambda engine: convert_manifest(
            engine,
            org_id=org_id,
            connection_id=connection_id,
            keys=_keys(),
            endpoint=_github(),
            now=datetime.now(UTC),
        )
    )


@task(REFRESH_CONNECTION_TASK)
def refresh_task(*, org_id: str, connection_id: str) -> None:
    with_engine(
        lambda engine: refresh(
            engine, org_id=org_id, connection_id=connection_id, keys=_keys(), now=datetime.now(UTC)
        )
    )


@task(CHECK_CONNECTION_TASK)
def check_task(*, org_id: str, connection_id: str, request_id: str | None = None) -> None:
    with_engine(
        lambda engine: check(
            engine,
            org_id=org_id,
            connection_id=connection_id,
            keys=_keys(),
            now=datetime.now(UTC),
            request_id=request_id,
        )
    )
