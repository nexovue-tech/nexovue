"""Reading check definitions out of Postgres, whole, for beat to hand over.

The `checks` table and its neighbours are owned by the API, which is where
customers create and edit rows. This service only reads them, and by name rather
than through the API's models: the two deploy separately, and a scheduler that
cannot start because a control-plane package moved is a scheduler that is not
running anyone's checks.

Everything a run needs is gathered here — variables, secrets still sealed, file
names, maintenance windows — so the task beat dispatches carries the whole
definition and the worker never reads Postgres on the path of a measurement.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.checks.config import parse_config, secret_names_of
from nexovue_platform.checks.sealing import encode_sealed
from nexovue_platform.checks.task import CheckDefinition, FileReference, MaintenanceSchedule
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

HEARTBEAT_TYPE: Final = "heartbeat"

# Only enabled rows, and only the columns a run needs. `interval_seconds` is
# validated here rather than trusted: it becomes beat's interval, and a
# zero would spin.
_ENABLED_CHECKS: Final = text(
    """
    SELECT id, org_id, project_id, name, type, interval_seconds, config, locations,
           degraded_ms, failing_ms, retries, created_at, last_ping_at
    FROM checks
    WHERE enabled = true AND (CAST(:only_type AS text) IS NULL OR type = :only_type)
    ORDER BY id
    """
)
_VARIABLES: Final = text(
    "SELECT project_id, name, value FROM check_variables WHERE project_id = ANY(:projects)"
)
_SECRETS: Final = text(
    "SELECT project_id, name, sealed_value FROM check_secrets WHERE project_id = ANY(:projects)"
)
_FILES: Final = text(
    """
    SELECT id, check_id, name, content_type, size_bytes
    FROM check_files WHERE check_id = ANY(:checks)
    """
)
_WINDOWS: Final = text(
    """
    SELECT w.id, w.project_id, w.starts_at, w.ends_at, w.recurrence, w.repeat_until,
           w.applies_to_all,
           array_remove(array_agg(c.check_id), NULL) AS check_ids
    FROM maintenance_windows w
    LEFT JOIN maintenance_window_checks c ON c.window_id = w.id
    WHERE w.project_id = ANY(:projects)
    GROUP BY w.id
    """
)


@dataclass(frozen=True, slots=True)
class ScheduledCheck:
    """One enabled check, as beat schedules it."""

    id: uuid.UUID
    type: str
    interval_seconds: int
    definition: CheckDefinition
    created_at: datetime
    last_ping_at: datetime | None

    @property
    def is_heartbeat(self) -> bool:
        return self.type == HEARTBEAT_TYPE

    def schedule_name(self, location: str) -> str:
        """The row id and the location: two checks may watch the same URL, and
        one check runs once per location."""
        return f"check:{self.id}:{location}"


async def load_enabled_checks(
    engine: AsyncEngine, *, all_locations: list[str], only_type: str | None = None
) -> list[ScheduledCheck]:
    """Every enabled check, whole, for one refresh of the beat schedule.

    A row with a non-positive interval is skipped rather than raising. One bad
    row must not stop every other customer's checks from being scheduled.
    """
    async with engine.connect() as connection:
        rows = [
            row
            for row in (
                await connection.execute(_ENABLED_CHECKS, {"only_type": only_type})
            ).mappings()
            if row["interval_seconds"] > 0
        ]
        if not rows:
            return []
        related = await _related(connection, rows)

    return [_scheduled(row, related, all_locations) for row in rows]


@dataclass(frozen=True, slots=True)
class _Related:
    variables: dict[uuid.UUID, dict[str, str]]
    secrets: dict[uuid.UUID, dict[str, bytes]]
    files: dict[uuid.UUID, dict[str, FileReference]]
    windows: list[Any]


async def _related(connection: AsyncConnection, rows: list[Any]) -> _Related:
    projects = list({row["project_id"] for row in rows})
    checks = [row["id"] for row in rows]

    variables: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    for row in (await connection.execute(_VARIABLES, {"projects": projects})).mappings():
        variables[row["project_id"]][row["name"]] = row["value"]

    secrets: dict[uuid.UUID, dict[str, bytes]] = defaultdict(dict)
    for row in (await connection.execute(_SECRETS, {"projects": projects})).mappings():
        secrets[row["project_id"]][row["name"]] = bytes(row["sealed_value"])

    files: dict[uuid.UUID, dict[str, FileReference]] = defaultdict(dict)
    for row in (await connection.execute(_FILES, {"checks": checks})).mappings():
        files[row["check_id"]][str(row["id"])] = FileReference(
            name=row["name"], content_type=row["content_type"], size_bytes=row["size_bytes"]
        )

    windows = list((await connection.execute(_WINDOWS, {"projects": projects})).mappings())
    return _Related(variables=variables, secrets=secrets, files=files, windows=windows)


def _scheduled(row: Any, related: _Related, all_locations: list[str]) -> ScheduledCheck:
    project_id = row["project_id"]
    config = row["config"] or {}
    try:
        wanted_secrets = secret_names_of(parse_config(config))
    except ValidationError:
        # Carried as stored; the run reports it misconfigured, which is where the
        # customer will see it.
        wanted_secrets = frozenset()

    definition = CheckDefinition(
        check_id=str(row["id"]),
        org_id=str(row["org_id"]),
        project_id=str(project_id),
        name=row["name"],
        type=row["type"],
        config=config,
        interval_seconds=row["interval_seconds"],
        locations=list(row["locations"]) or list(all_locations),
        degraded_ms=row["degraded_ms"],
        failing_ms=row["failing_ms"],
        retries=row["retries"],
        variables=dict(sorted(related.variables.get(project_id, {}).items())),
        sealed_secrets={
            name: encode_sealed(sealed)
            for name, sealed in sorted(related.secrets.get(project_id, {}).items())
            if name in wanted_secrets
        },
        files=related.files.get(row["id"], {}),
        maintenance=[
            MaintenanceSchedule(
                starts_at=window["starts_at"].isoformat(),
                ends_at=window["ends_at"].isoformat(),
                recurrence=window["recurrence"],
                repeat_until=(
                    window["repeat_until"].isoformat() if window["repeat_until"] else None
                ),
            )
            for window in related.windows
            if window["project_id"] == project_id
            and (window["applies_to_all"] or row["id"] in (window["check_ids"] or []))
        ],
    )
    return ScheduledCheck(
        id=row["id"],
        type=row["type"],
        interval_seconds=row["interval_seconds"],
        definition=definition,
        created_at=row["created_at"],
        last_ping_at=row["last_ping_at"],
    )
