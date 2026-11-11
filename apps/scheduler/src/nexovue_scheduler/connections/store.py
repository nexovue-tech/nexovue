"""Loading a connection and writing its state, shared by every connection job (F22).

Each statement binds the job's own `org_id` and the connection's id (AC129): a job that
names another organization's connection finds no row and stops before any call. A
credential is opened in memory only, and a key that will not open is the
`key_unreadable` failure a person must fix (design, *Failure handling*).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.connections import (
    CONNECTION_CREDENTIAL_PURPOSE,
    TRACKER_KINDS,
    WORKING,
    connection_secret_owner,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import states
from nexovue_scheduler.connections.trackers import StoredConnection, TrackerFailure

logger = get_logger(__name__)

_LOAD: Final = text(
    """
    SELECT id, org_id, kind, state, sealed_credential, sealed_manifest_code, setup_expires_at,
           github_app_id, github_app_slug, github_installation_id, paused_until,
           consecutive_failures, failing_since, last_failure, last_failure_status
    FROM connections
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
# A connection back at `working` ends its failure episode, so the next one is told
# again (FR33, AC78): the marker clears with the state.
_STAND: Final = text(
    """
    UPDATE connections
    SET state = :state, consecutive_failures = :consecutive_failures,
        failing_since = :failing_since, last_failure = :last_failure,
        last_failure_status = :last_failure_status,
        last_ok_at = COALESCE(:last_ok_at, last_ok_at),
        failure_told_at = CASE WHEN :state = :working THEN NULL ELSE failure_told_at END
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)


@dataclass(frozen=True, slots=True)
class Loaded:
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


async def load_connection(engine: AsyncEngine, org_id: str, connection_id: str) -> Loaded | None:
    """The live tracker connection, in this job's organization only."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(_LOAD, {"org_id": org_id, "connection_id": connection_id})
        ).one_or_none()
    if row is None or row.kind not in TRACKER_KINDS:
        logger.info("a connection job found no live connection", connection_id=connection_id)
        return None
    return Loaded(row)


def box(keys: ConfiguredKeys, purpose: str) -> SealedBox:
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose)


def stored_connection(loaded: Loaded, keys: ConfiguredKeys) -> StoredConnection:
    """The row's facts and its opened credential; `TrackerFailure` if it will not open."""
    row = loaded.row
    if row.sealed_credential is None:
        raise TrackerFailure("app_not_installed")
    try:
        credential = box(keys, CONNECTION_CREDENTIAL_PURPOSE).open(
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
        github_app_slug=row.github_app_slug,
    )


async def write_standing(
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
                "working": WORKING,
            },
        )
