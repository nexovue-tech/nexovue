"""A target's months, *Last month* across a project's targets, the CSV and the history
(F19 FR27, FR27a, FR27b, FR27c, FR30; plan T3.3).

**Postgres alone.** A month is summed from the target's stored days by the platform's
one function (`nexovue_platform.slo.month_figures`), so it still shows once the rollups
have expired (AC44), and the scheduler, the screen and the CSV cannot disagree.

**What a month leaves out** is stated with it: coverage, quiet, lost and excluded
time, a sharp fall in requests (FR27c), each traffic finding or silence incident
on the target's service while those records last (FR27a), and each exclusion removed
too late for its days to be counted again: "removed, figure not recounted" (FR29, AC69).

**A deleted target still counts** in every month that ended before its deletion: from
its own days while it can be restored, then from `slo_deleted_months` once purged
(D46, AC112). A target deleted before a month ended is not in that month.

**The CSV** carries what the copy carries: the period, what it covers, quiet and
excluded time, the sampling answer and the fixed label by kind (FR27b, AC78), with
every customer-text cell made safe for a spreadsheet (AC115). Complete months only.
"""

from __future__ import annotations

import csv
import io
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from nexovue_platform import slo
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Reader
from nexovue_api.data.postgres.models import (
    Finding,
    Incident,
    SloDeletedMonth,
    SloExclusion,
    SloTarget,
)
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.exports.csv_cells import spreadsheet_safe
from nexovue_api.projects.service import require_readable_project
from nexovue_api.slos import queries
from nexovue_api.slos.schemas import (
    OutageKind,
    SloHistory,
    SloHistoryEntry,
    SloLastMonth,
    SloMonth,
    SloMonthRow,
    SloMonths,
    SloOutage,
    SloRemovedExclusion,
    SloSharpFall,
)
from nexovue_api.slos.service import NO_SUCH_TARGET, day_counts, label_of, zone_of

# FR27c compares a day with the same weekday in up to four earlier weeks, so a month's
# days are read with the four weeks before it.
_WEEKS_BEFORE: Final = timedelta(weeks=slo.SHARP_FALL_WEEKS_COMPARED)
# The CSV reaches back at most as far as months are kept (founder decision 20).
CSV_MONTHS_MOST: Final = slo.MONTHS_SHOWN
CSV_COLUMNS: Final = (
    "month",
    "target",
    "kind",
    "result",
    "figure_percent",
    "objective_percent",
    "measured_percent",
    "good",
    "total",
    "quiet_minutes",
    "longest_quiet_minutes",
    "lost_minutes",
    "excluded_minutes",
    "missed_runs",
    "maintenance_runs",
    "days_held",
    "days_in_month",
    "may_still_change",
    "loss_known",
    "sampled_before_nexovue",
    "deleted_on",
    "label",
    "audience",
)
_PERCENT: Final = 100
# Results the *Last month* rows lead with: what needs a reader first.
_ROW_ORDER: Final = {
    slo.MonthResult.MISSED: 0,
    slo.MonthResult.NOT_JUDGED: 1,
    slo.MonthResult.MET: 2,
}


# ------------------------------------------------------------------------- reads


async def target_months(
    db: AsyncSession, *, reader: Reader, target_id: uuid.UUID, now: datetime
) -> SloMonths:
    """A live target's months, newest first, up to 13 (FR27)."""
    row = await queries.target_with_status(db, org_id=reader.org_id, target_id=target_id)
    if row is None or row[0].deleted_at is not None:
        raise NotFoundError(NO_SUCH_TARGET)
    target: SloTarget = row[0]
    project = await require_readable_project(db, reader=reader, project_id=target.project_id)
    zone = zone_of(project)
    since = slo.earliest_month_shown(now.astimezone(zone).date()) - _WEEKS_BEFORE
    days = await _days(db, reader.org_id, [target], since)
    figures = slo.months_of(
        days.get(target.id, {}),
        kind=target.kind,
        objective=target.objective_percent,
        definition_version=target.definition_version,
        first_event_at=target.first_event_at,
        now=now,
        zone=zone,
    )
    outages = await _outages(
        db,
        reader.org_id,
        target.project_id,
        [target],
        start=slo.day_bounds(figures[-1].month, zone)[0] if figures else now,
        end=now,
    )
    removals = await _removals(
        db,
        reader.org_id,
        [target],
        since=slo.day_bounds(figures[-1].month, zone)[0] if figures else now,
    )
    return SloMonths(
        id=target.id,
        label=label_of(target, row[3]),
        kind=target.kind,
        upstream_sampling=target.upstream_sampling,
        report_label=slo.report_label(target.kind),
        audience=slo.AUDIENCE_LINE,
        zone=str(zone),
        months=[
            _month(
                month,
                target,
                outages.get(target.service_name or "", []),
                removals.get(target.id, []),
                zone,
            )
            for month in figures
        ],
    )


async def last_month(
    db: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    month: date | None,
    now: datetime,
) -> SloLastMonth:
    """FR27b: one month across a project's targets, the last complete one by default."""
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    zone = zone_of(project)
    shown = slo.month_start(month) if month else _last_complete_month(now, zone)
    rows = await _month_rows(db, reader.org_id, project_id, [shown], zone=zone, now=now)
    counted = {result: sum(row.result is result for row in rows) for result in slo.MonthResult}
    return SloLastMonth(
        month=shown,
        zone=str(zone),
        audience=slo.AUDIENCE_LINE,
        targets=len(rows),
        met=counted[slo.MonthResult.MET],
        missed=counted[slo.MonthResult.MISSED],
        not_judged=counted[slo.MonthResult.NOT_JUDGED],
        met_with_time_not_counted=sum(
            row.result is slo.MonthResult.MET and row.time_not_counted for row in rows
        ),
        rows=sorted(rows, key=lambda row: (_ROW_ORDER[row.result], row.label)),
    )


async def months_csv(
    db: AsyncSession,
    *,
    reader: Reader,
    project_id: uuid.UUID,
    months: int,
    target_id: uuid.UUID | None,
    now: datetime,
) -> tuple[str, str]:
    """The last `months` complete months as CSV, and the project's slug for its name."""
    project = await require_readable_project(db, reader=reader, project_id=project_id)
    zone = zone_of(project)
    shown = [_last_complete_month(now, zone)]
    while len(shown) < months:
        shown.append(slo.previous_month(shown[-1]))
    rows = await _month_rows(db, reader.org_id, project_id, shown, zone=zone, now=now)
    if target_id is not None:
        rows = [row for row in rows if row.id == target_id]
    sheet = io.StringIO()
    writer = csv.writer(sheet)
    writer.writerow(CSV_COLUMNS)
    for row in sorted(rows, key=lambda row: (row.month, row.label), reverse=True):
        writer.writerow(_csv_cells(row, zone))
    return sheet.getvalue(), project.slug


async def history(db: AsyncSession, *, reader: Reader, target_id: uuid.UUID) -> SloHistory:
    """FR30: each change, live or deleted target, the person named to a person only."""
    target = await queries.target_for_read(db, org_id=reader.org_id, target_id=target_id)
    if target is None:
        raise NotFoundError(NO_SUCH_TARGET)
    await require_readable_project(db, reader=reader, project_id=target.project_id)
    changes = await queries.changes_of(db, org_id=reader.org_id, target_id=target_id)
    return SloHistory(
        entries=[
            SloHistoryEntry(
                at=change.at,
                what=change.what,
                before=change.before,
                after=change.after,
                by_name=None if reader.is_token else name,
            )
            for change, name in changes
        ]
    )


# ----------------------------------------------------------------------- helpers


def _last_complete_month(now: datetime, zone: ZoneInfo) -> date:
    return slo.previous_month(slo.month_start(now.astimezone(zone).date()))


async def _days(
    db: AsyncSession, org_id: uuid.UUID, targets: Sequence[SloTarget], since: date
) -> dict[uuid.UUID, dict[date, slo.DayCounts]]:
    rows = await queries.days_since(
        db, org_id=org_id, target_ids=[target.id for target in targets], since=since
    )
    found: dict[uuid.UUID, dict[date, slo.DayCounts]] = {}
    for row in rows:
        found.setdefault(row.slo_id, {})[row.day] = day_counts(row)
    return found


@dataclass(frozen=True, slots=True)
class _Outage:
    kind: OutageKind
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime


async def _outages(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    targets: Iterable[SloTarget],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, list[_Outage]]:
    """FR27a: each request kind's service's traffic findings and silence incidents in
    [start, end), one read of each for all the targets."""
    # A check target may name a service to join findings (D19), but counts runs, not
    # requests, so a service's outage is not what its month leaves out.
    services = sorted(
        {
            target.service_name
            for target in targets
            if target.service_name and target.kind in slo.REQUEST_KINDS
        }
    )
    found: dict[str, list[_Outage]] = {}
    for finding in await queries.traffic_findings(
        db, org_id=org_id, project_id=project_id, services=services, start=start, end=end
    ):
        found.setdefault(finding.service_name or "", []).append(_finding_outage(finding, end))
    for incident in await queries.silence_incidents(
        db, org_id=org_id, project_id=project_id, services=services, start=start, end=end
    ):
        service = incident.subject_key.removeprefix("service:")
        found.setdefault(service, []).append(_incident_outage(incident, end))
    return found


def _finding_outage(finding: Finding, now: datetime) -> _Outage:
    return _Outage("traffic_finding", finding.id, finding.started_at, finding.closed_at or now)


def _incident_outage(incident: Incident, now: datetime) -> _Outage:
    return _Outage("silence_incident", incident.id, incident.opened_at, incident.resolved_at or now)


async def _removals(
    db: AsyncSession, org_id: uuid.UUID, targets: Sequence[SloTarget], *, since: datetime
) -> dict[uuid.UUID, list[SloExclusion]]:
    """FR29, AC69: each target's exclusions removed past the recount limit that end
    after `since`, one read for all the targets."""
    found: dict[uuid.UUID, list[SloExclusion]] = {}
    for exclusion in await queries.removed_not_recounted(
        db, org_id=org_id, target_ids=[target.id for target in targets], since=since
    ):
        found.setdefault(exclusion.slo_id, []).append(exclusion)
    return found


def _removed_in_month(
    removals: Iterable[SloExclusion], month: date, zone: ZoneInfo
) -> list[SloRemovedExclusion]:
    """The removals not recounted whose time overlaps a month, each with its whole
    length: the month's days past the recount limit still leave that time out."""
    start, end = slo.day_bounds(month, zone)[0], slo.day_bounds(slo.next_month(month), zone)[0]
    return [
        SloRemovedExclusion(
            id=removal.id,
            starts_at=removal.starts_at,
            minutes=int((removal.ends_at - removal.starts_at).total_seconds() // 60),
            removed_at=removal.removed_at,
        )
        for removal in removals
        if removal.removed_at is not None and removal.starts_at < end and removal.ends_at > start
    ]


def _in_month(outages: Iterable[_Outage], month: date, zone: ZoneInfo) -> list[SloOutage]:
    """The outages overlapping a month, each with its whole length."""
    start, end = slo.day_bounds(month, zone)[0], slo.day_bounds(slo.next_month(month), zone)[0]
    return [
        SloOutage(
            kind=outage.kind,
            id=outage.id,
            started_at=outage.started_at,
            minutes=int((outage.ended_at - outage.started_at).total_seconds() // 60),
        )
        for outage in sorted(outages, key=lambda outage: outage.started_at)
        if outage.started_at < end and outage.ended_at > start
    ]


def _month(
    figures: slo.MonthFigures,
    target: SloTarget,
    outages: Iterable[_Outage],
    removals: Iterable[SloExclusion],
    zone: ZoneInfo,
) -> SloMonth:
    return SloMonth(**_month_fields(figures, target.objective_percent, outages, removals, zone))


def _month_fields(
    figures: slo.MonthFigures,
    objective: Any,
    outages: Iterable[_Outage],
    removals: Iterable[SloExclusion],
    zone: ZoneInfo,
) -> dict[str, Any]:
    shown = _in_month(outages, figures.month, zone)
    return {
        "month": figures.month,
        "result": figures.result,
        "figure_percent": figures.figure_percent,
        "objective_percent": objective,
        "good": figures.good,
        "total": figures.total,
        "failures": figures.failures,
        "coverage": figures.coverage,
        "quiet_minutes": figures.quiet_minutes,
        "longest_quiet_minutes": figures.longest_quiet_minutes,
        "lost_minutes": figures.lost_minutes,
        "excluded_minutes": figures.excluded_minutes,
        "missed_runs": figures.missed_runs,
        "maintenance_runs": figures.maintenance_runs,
        "days_held": figures.days_held,
        "days_in_month": figures.days_in_month,
        "may_still_change": figures.may_still_change,
        "loss_known": figures.loss_known,
        "changed_at": figures.changed_at,
        "changed_why": figures.changed_why,
        "earlier_definition_through": figures.earlier_definition_through,
        "sharp_falls": [
            SloSharpFall(day=fall.day, fall_percent=fall.fall_percent)
            for fall in figures.sharp_falls
        ],
        "outages": shown,
        "removed_not_recounted": _removed_in_month(removals, figures.month, zone),
        "time_not_counted": bool(figures.lost_minutes or figures.excluded_minutes or shown),
    }


async def _month_rows(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    months: Sequence[date],
    *,
    zone: ZoneInfo,
    now: datetime,
) -> list[SloMonthRow]:
    """Each month's row for every target it counts: live, deleted after it ended, or
    purged after it ended (D46). A target holding no day of a month is not in it."""
    oldest = min(months)
    targets = [
        row
        for row in await queries.targets_with_status(db, org_id=org_id, project_id=project_id)
        if row[0].deleted_at is None
        or row[0].deleted_at >= slo.day_bounds(slo.next_month(oldest), zone)[0]
    ]
    days = await _days(db, org_id, [row[0] for row in targets], oldest - _WEEKS_BEFORE)
    outages = await _outages(
        db,
        org_id,
        project_id,
        [row[0] for row in targets],
        start=slo.day_bounds(oldest, zone)[0],
        end=now,
    )
    removals = await _removals(
        db, org_id, [row[0] for row in targets], since=slo.day_bounds(oldest, zone)[0]
    )
    purged = await queries.deleted_months_since(
        db, org_id=org_id, project_id=project_id, since=oldest
    )
    rows: list[SloMonthRow] = []
    for month in months:
        ended = slo.day_bounds(slo.next_month(month), zone)[0]
        for row in targets:
            target: SloTarget = row[0]
            if target.deleted_at is not None and target.deleted_at < ended:
                continue
            figures = slo.month_figures(
                days.get(target.id, {}),
                month=month,
                kind=target.kind,
                objective=target.objective_percent,
                definition_version=target.definition_version,
                first_event_at=target.first_event_at,
                now=now,
                zone=zone,
            )
            if figures is not None:
                rows.append(
                    _live_row(
                        figures,
                        target,
                        row[3],
                        outages.get(target.service_name or "", []),
                        removals.get(target.id, []),
                        zone,
                    )
                )
        rows.extend(
            _purged_row(record)
            for record in purged
            if record.month == month and record.deleted_at >= ended
        )
    return rows


def _live_row(
    figures: slo.MonthFigures,
    target: SloTarget,
    check_name: str | None,
    outages: Iterable[_Outage],
    removals: Iterable[SloExclusion],
    zone: ZoneInfo,
) -> SloMonthRow:
    return SloMonthRow(
        **_month_fields(figures, target.objective_percent, outages, removals, zone),
        id=target.id,
        name=target.name,
        label=label_of(target, check_name),
        kind=target.kind,
        upstream_sampling=target.upstream_sampling,
        report_label=slo.report_label(target.kind),
        deleted_at=target.deleted_at,
    )


def _purged_row(record: SloDeletedMonth) -> SloMonthRow:
    """A purged target's month as the purge kept it: its figures, not its outage links,
    sharp falls or removed exclusions, whose own records went with it or may be gone
    (FR27a: "while those records last")."""
    return SloMonthRow(
        month=record.month,
        result=slo.MonthResult(record.result),
        figure_percent=record.figure_percent,
        objective_percent=record.objective_percent,
        good=record.good,
        total=record.total,
        failures=record.total - record.good,
        coverage=record.coverage,
        quiet_minutes=record.quiet_minutes,
        longest_quiet_minutes=record.longest_quiet_minutes,
        lost_minutes=record.lost_minutes,
        excluded_minutes=record.excluded_minutes,
        missed_runs=record.missed_runs,
        maintenance_runs=record.maintenance_runs,
        days_held=record.days_held,
        days_in_month=record.days_in_month,
        may_still_change=False,
        loss_known=record.loss_known,
        changed_at=None,
        changed_why=None,
        earlier_definition_through=None,
        sharp_falls=[],
        outages=[],
        removed_not_recounted=[],
        time_not_counted=bool(record.lost_minutes or record.excluded_minutes),
        id=record.slo_id,
        name=None,
        label=record.label,
        kind=record.kind,
        upstream_sampling=record.upstream_sampling,
        report_label=slo.report_label(record.kind),
        deleted_at=record.deleted_at,
    )


def _csv_cells(row: SloMonthRow, zone: ZoneInfo) -> list[object]:
    return [
        row.month.isoformat()[:7],
        spreadsheet_safe(row.label),
        row.kind,
        row.result,
        _blank(row.figure_percent),
        row.objective_percent,
        "" if row.coverage is None else math.floor(row.coverage * _PERCENT),
        row.good,
        row.total,
        row.quiet_minutes,
        row.longest_quiet_minutes,
        row.lost_minutes,
        row.excluded_minutes,
        _blank(row.missed_runs),
        _blank(row.maintenance_runs),
        row.days_held,
        row.days_in_month,
        _yes_no(row.may_still_change),
        _yes_no(row.loss_known),
        row.upstream_sampling or "",
        "" if row.deleted_at is None else row.deleted_at.astimezone(zone).date().isoformat(),
        row.report_label,
        slo.AUDIENCE_LINE,
    ]


def _blank(value: object | None) -> object:
    return "" if value is None else value


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
