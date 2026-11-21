"""What reliability targets keep, and for how long (F19 FR31, D46; design, *Inventories*).

The expiry sweep (`jobs/expiry.py`) runs both of these:

- **The purge.** A target deleted more than 30 days ago can no longer be restored, so
  its daily figures go. First, each month that ended before its deletion is written to
  `slo_deleted_months`, by the platform's one month function, so a past month still
  counts it and "5 of 7" cannot become "5 of 6" by deleting a miss (AC112). Then the
  target goes, and its status and days cascade with it. Its change history stays
  (`slo_target_changes` has no key to it). One transaction per target, so a month is
  never written for a target left standing, nor a target removed without its months.
- **Lapsing at 400 days**, which holds 13 months (founder decision 20): daily
  figures by their day, changes by when they were made, and a purged month once 400
  days have passed since it ended (AC88), and a removed exclusion 400 days after its
  removal; a live one stays with its target (FR29).

A project or organization deletion removes all of this at once, with no wait
(`organizations/tables.py`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from nexovue_platform.detection import DEFAULT_TIMEZONE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# Daily figures, changes and purged months are kept this long (design, *Inventories*).
KEPT_DAYS: Final = 400
# Targets purged in one sweep, so a backlog is cleared in bites (`jobs/expiry.py`).
PURGE_BATCH: Final = 100

# Every organization's targets past restoring, oldest deletion first: the sweep's work.
_DUE_FOR_PURGE: Final = text(
    """
    SELECT org_id, id FROM slo_targets
    WHERE deleted_at < :cutoff
    ORDER BY deleted_at, id
    LIMIT :batch
    """
)
# The target as its months need it, locked so a restore racing the purge waits for it.
_PURGED_TARGET: Final = text(
    """
    SELECT t.id, t.project_id, t.kind, t.service_name, t.endpoint_name, t.boundary_index,
           t.objective_percent, t.window_days, t.upstream_sampling, t.definition_version,
           t.first_event_at, t.deleted_at, c.name AS check_name, p.timezone
    FROM slo_targets AS t
    JOIN projects AS p ON p.org_id = t.org_id AND p.id = t.project_id
    LEFT JOIN checks AS c ON c.org_id = t.org_id AND c.id = t.check_id
    WHERE t.org_id = :org_id AND t.id = :id AND t.deleted_at < :cutoff
    FOR UPDATE OF t
    """
)
_PURGED_DAYS: Final = text(
    """
    SELECT day, good, total, lost_minutes, excluded_minutes, expected_runs, missed_runs,
           maintenance_runs, quiet_minutes, quiet_leading_minutes, quiet_trailing_minutes,
           longest_quiet_minutes, loss_known, final, definition_version, changed_at,
           changed_why
    FROM slo_daily_counts
    WHERE org_id = :org_id AND slo_id = :id
    """
)
# Once per month: a purge run again after a crash writes nothing twice.
_KEEP_MONTH: Final = text(
    """
    INSERT INTO slo_deleted_months (
        org_id, project_id, slo_id, month, kind, label, objective_percent, result,
        figure_percent, good, total, coverage, quiet_minutes, longest_quiet_minutes,
        lost_minutes, excluded_minutes, missed_runs, maintenance_runs, days_held,
        days_in_month, loss_known, upstream_sampling, deleted_at)
    VALUES (
        :org_id, :project_id, :slo_id, :month, :kind, :label, :objective, :result,
        :figure, :good, :total, :coverage, :quiet, :longest_quiet, :lost, :excluded,
        :missed_runs, :maintenance_runs, :days_held, :days_in_month, :loss_known,
        :sampling, :deleted_at)
    ON CONFLICT (org_id, slo_id, month) DO NOTHING
    """
)
_PURGE: Final = text("DELETE FROM slo_targets WHERE org_id = :org_id AND id = :id")

# (table, the columns that name a row, what makes a row lapsed), as `jobs/expiry.py`
# reads them.
EXPIRED: Final = (
    ("slo_daily_counts", "org_id, slo_id, day", f"day < CURRENT_DATE - {KEPT_DAYS}"),
    ("slo_target_changes", "id", f"at < now() - interval '{KEPT_DAYS} days'"),
    (
        "slo_deleted_months",
        "org_id, slo_id, month",
        f"month + interval '1 month' < now() - interval '{KEPT_DAYS} days'",
    ),
    ("slo_exclusions", "id", f"removed_at < now() - interval '{KEPT_DAYS} days'"),
)


async def purge_deleted(engine: AsyncEngine, *, now: datetime) -> int:
    """Purge every target past restoring, in batches. Returns how many went."""
    cutoff = now - slo.RESTORE_FOR
    purged = 0
    while True:
        async with engine.connect() as connection:
            due = list(
                await connection.execute(_DUE_FOR_PURGE, {"cutoff": cutoff, "batch": PURGE_BATCH})
            )
        for row in due:
            async with engine.begin() as connection:
                purged += await _purge_one(connection, row.org_id, row.id, cutoff=cutoff, now=now)
        if len(due) < PURGE_BATCH:
            return purged


async def _purge_one(
    connection: AsyncConnection,
    org_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    cutoff: datetime,
    now: datetime,
) -> int:
    keys = {"org_id": org_id, "id": target_id}
    target = (await connection.execute(_PURGED_TARGET, keys | {"cutoff": cutoff})).one_or_none()
    if target is None:
        # Restored or purged since the sweep looked.
        return 0
    days = {
        row.day: slo.DayCounts.of_row(row) for row in await connection.execute(_PURGED_DAYS, keys)
    }
    for month in complete_months(target, days, now=now):
        await connection.execute(_KEEP_MONTH, _month_parameters(org_id, target, month))
    await connection.execute(_PURGE, keys)
    return 1


def complete_months(
    target: Any, days: dict[date, slo.DayCounts], *, now: datetime
) -> list[slo.MonthFigures]:
    """The target's months that ended before its deletion (FR31: "each complete month")."""
    zone = ZoneInfo(target.timezone or DEFAULT_TIMEZONE)
    return [
        month
        for month in slo.months_of(
            days,
            kind=target.kind,
            objective=target.objective_percent,
            definition_version=target.definition_version,
            first_event_at=target.first_event_at,
            now=now,
            zone=zone,
        )
        if slo.day_bounds(slo.next_month(month.month), zone)[0] <= target.deleted_at
    ]


def _month_parameters(org_id: uuid.UUID, target: Any, month: slo.MonthFigures) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "project_id": target.project_id,
        "slo_id": target.id,
        "month": month.month,
        "kind": target.kind,
        "label": slo.subject_label(
            target.kind,
            objective=target.objective_percent,
            window_days=target.window_days,
            service_name=target.service_name,
            endpoint_name=target.endpoint_name,
            check_name=target.check_name or slo.DELETED_CHECK_NAME,
            boundary_index=target.boundary_index,
        ),
        "objective": target.objective_percent,
        "result": month.result.value,
        "figure": month.figure_percent,
        "good": month.good,
        "total": month.total,
        "coverage": month.coverage,
        "quiet": month.quiet_minutes,
        "longest_quiet": month.longest_quiet_minutes,
        "lost": month.lost_minutes,
        "excluded": month.excluded_minutes,
        "missed_runs": month.missed_runs,
        "maintenance_runs": month.maintenance_runs,
        "days_held": month.days_held,
        "days_in_month": month.days_in_month,
        "loss_known": month.loss_known,
        "sampling": target.upstream_sampling,
        "deleted_at": target.deleted_at,
    }
