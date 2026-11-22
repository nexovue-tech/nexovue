"""What a monitor reads about the scheduler (ADR-021), served by `health.py`.

Two kinds. Every process reports its own: how long since its loop beat, and —
beat — whether it leads; workers, how their jobs ended. Beat's leader alone
reads the queues every `MEASURE_SECONDS`, because they are the same rows
whichever process asks: jobs ready per queue and how long the oldest has
waited, alerts waiting to be delivered, erasures overdue, and detection's
progress — how old the newest settled minute is, how many projects it has
stopped judging, how long the stalest has waited, and the events it recorded
about nexovue's own intake (F6, C8), whether a tracker connection is live
(F22), F3's explanations and AI runs, and F5's suggested fixes: rows by state and reason,
the oldest in flight, the tamper signal and how long since rung 3's sweep finished. Those are
read from
Postgres, not counted where they happen, because the sweep runs in a worker's child
process, whose metrics no scrape sees.

The queue readings are across every organization on purpose: they are counts of
our own work, and no label names a tenant. A project detection has stopped
judging is named in the scheduler's log, never here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nexovue_platform.connections import TRACKER_KINDS
from nexovue_platform.detection import NOT_RUNNING_AFTER
from nexovue_platform.fixes.records import APPROVED, FIX_SWEEP_NAME, IN_FLIGHT_STATES
from nexovue_platform.slo import STALE_AFTER as SLO_STALE_AFTER
from prometheus_client import Counter, Gauge, disable_created_metrics
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nexovue_scheduler.detection.settle import EXPIRED_EVENT, GUARD_EVENT
from nexovue_scheduler.erasure import owed

MEASURE_SECONDS: Final = 15.0

# A `_created` timestamp beside every counter is noise to every monitor we read with.
disable_created_metrics()  # type: ignore[no-untyped-call]

# The claim's own idea of ready (`jobs/queue._CLAIM`): due, not leased, not
# expired, with an attempt left. Not ready is not waiting.
_JOBS_READY: Final = text(
    """
    SELECT queue, count(*) AS ready, EXTRACT(EPOCH FROM now() - min(run_after)) AS oldest
    FROM jobs
    WHERE run_after <= now()
      AND (lease_until IS NULL OR lease_until < now())
      AND (expires_at IS NULL OR expires_at > now())
      AND attempts < max_attempts
    GROUP BY queue
    """
)

# The outbox claim's own predicate (`alerts/outbox.py`): due and pending, or a
# delivery whose lease lapsed.
_ALERTS_WAITING: Final = text(
    """
    SELECT count(*) AS waiting,
           COALESCE(EXTRACT(EPOCH FROM now() - min(deliver_after)), 0) AS oldest
    FROM notifications
    WHERE (state = 'pending' AND deliver_after <= now())
       OR (state = 'delivering' AND lease_until < now())
    """
)

# The API's *not running*, install-wide: a project detection is expected to judge
# whose run has not completed for `NOT_RUNNING_AFTER`, counted from the later of
# its last run and when it became expected (D34). The lag is the stalest such
# clock, healthy projects included, so it reads small while all is well.
_DETECTION_PROJECTS: Final = text(
    """
    SELECT count(*) FILTER (
               WHERE behind_since < now() - CAST(:not_running AS interval)
           ) AS behind,
           COALESCE(EXTRACT(EPOCH FROM now() - min(behind_since)), 0) AS oldest
    FROM (
        SELECT GREATEST(last_completed_at, expected_since) AS behind_since
        FROM detection_project_runs
        WHERE expected_since IS NOT NULL
    ) AS expected
    """
)
# Null when no sweep has settled a minute: read as infinitely old.
_DETECTION_SETTLED_AGE: Final = text(
    "SELECT EXTRACT(EPOCH FROM now() - settled_minute) FROM detection_sweep"
)
# The operator's alert stays up this long after each event, as one page per event.
DETECTION_EVENTS_RECENT_SECONDS: Final = 15 * 60
DETECTION_EVENT_KINDS: Final = (GUARD_EVENT, EXPIRED_EVENT)
_DETECTION_EVENTS: Final = text(
    """
    SELECT kind, count(*) AS recent FROM detection_events
    WHERE at > now() - make_interval(secs => :recent_seconds)
    GROUP BY kind
    """
)

# F22: whether any tracker connection is live, as an EXISTS. F5's own App is not
# `scheduler-connections`' sync, so it does not count (design, *Observability*).
_CONNECTIONS_ACTIVE: Final = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM connections
        WHERE state <> 'disconnected' AND kind = ANY(CAST(:kinds AS text[]))
    )
    """
)

# F3 (design, *Observability*). Counts across every organization, no tenant label. A
# finding is unexplained when it has been open this long and no version of its facts exists.
UNEXPLAINED_AFTER_SECONDS: Final = 15 * 60
# The window the recent counts below cover; long enough for a rule's `for: 5m`.
F3_RECENT_SECONDS: Final = 15 * 60
_F3_READINGS: Final = text(
    """
    SELECT
        (SELECT count(*) FROM findings AS f
         WHERE f.state = 'open'
           AND f.started_at < now() - make_interval(secs => :unexplained_after)
           AND NOT EXISTS (
               SELECT 1 FROM finding_explanations AS e
               WHERE e.org_id = f.org_id AND e.finding_id = f.id
           )) AS unexplained,
        (SELECT count(*) FROM ai_connections WHERE enabled) AS connections_enabled,
        (SELECT coalesce(sum(reads_failed), 0) FROM finding_explanations
         WHERE read_at > now() - make_interval(secs => :recent)) AS reads_failed,
        (SELECT count(*) FROM finding_explanations
         WHERE read_at > now() - make_interval(secs => :recent)
           AND evidence -> 'items' @> '[{"kind": "absence", "values": {"reason": "truncated"}}]')
            AS truncated,
        (SELECT coalesce(sum(used_tokens), 0) FROM ai_usage_monthly
         WHERE month = date_trunc('month', now() AT TIME ZONE 'UTC')::date) AS tokens_month
    """
)
_AI_ON: Final = text("SELECT EXISTS (SELECT 1 FROM ai_connections WHERE enabled)")
_AI_RUNS_RECENT: Final = text(
    """
    SELECT purpose, outcome, count(*) AS runs FROM ai_runs
    WHERE started_at > now() - make_interval(secs => :recent)
    GROUP BY purpose, outcome
    """
)
# F5 (D18, decision 52): written suggestions the rules refused again on the same rules,
# lists and files, at a check run or the sweep's first recording, in the last hour. The
# one tamper signal: an upgrade or a moved file reads "Out of date" and never counts.
F5_REFUSED_AGAIN_SECONDS: Final = 60 * 60
_FIX_CHECKS_REFUSED: Final = text(
    """
    SELECT count(*) FROM finding_fixes
    WHERE reason = 'checks_refused_at_click'
      AND state_changed_at > now() - make_interval(secs => :recent)
    """
)

# F5 T9.1 (design, *Observability*; C7: a worker child's counters are never scraped, so the
# job figures are read from `finding_fixes`). Each state and reason is a fixed vocabulary,
# so no label names a tenant; a state with no reason is labelled `none`.
F5_RECENT_SECONDS: Final = 60 * 60
NO_REASON: Final = "none"
_FIX_ROWS_RECENT: Final = text(
    """
    SELECT kind, state, coalesce(reason, :no_reason) AS reason, count(*) AS rows
    FROM finding_fixes
    WHERE state_changed_at > now() - make_interval(secs => :recent)
    GROUP BY kind, state, coalesce(reason, :no_reason)
    """
)
# A row a job is still working on: gathering, waiting at the list, being written, or its
# draft being opened. 0 when none.
FIX_WORKING_STATES: Final = (*IN_FLIGHT_STATES, APPROVED)
_FIX_OLDEST_IN_FLIGHT: Final = text(
    """
    SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(state_changed_at)), 0)
    FROM finding_fixes WHERE state = ANY(CAST(:states AS text[]))
    """
)
# When `sweep_fixes` last finished a whole pass (migration 0085). Null before any: read as
# infinitely old, as detection's settled minute is.
_FIX_SWEEP_AGE: Final = text(
    "SELECT EXTRACT(EPOCH FROM now() - finished_at) FROM fix_sweep_status WHERE sweep = :sweep"
)

# F19 (design, *Observability and operations*). Across every organization, no tenant label.
# A target's answer is as old as the minute the job last judged it; one never judged ages from
# its creation, as the screens' *Not up to date* does (`slo.State.NOT_UP_TO_DATE`). The same
# live projects the job is handed out for, so a deleted target or project is not read.
_SLO_STATUS: Final = text(
    """
    SELECT count(*) FILTER (WHERE age > :late_after) AS late, coalesce(max(age), 0) AS oldest
    FROM (
        SELECT EXTRACT(EPOCH FROM now() - coalesce(s.evaluated_minute, t.created_at)) AS age
        FROM slo_targets AS t
        JOIN project_retention AS live
            ON live.org_id = t.org_id AND live.project_id = t.project_id
        LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
        WHERE t.deleted_at IS NULL
    ) AS answers
    """
)
# What a burn held back (FR22, FR26) and has not yet released: the partial index
# `ix_notifications_withheld_unreleased` serves it, so it reads no more rows than are held. It
# is read here, not counted where it happens, because the claim runs in a worker's child
# process. How many were released lately is not read: no index serves `released_at`, so it
# would scan the whole delivery log every 15 seconds (`slo-tracking/status.md`, open decision 5).
_SLO_WITHHELD: Final = text(
    "SELECT count(*) FROM notifications WHERE withheld_why IS NOT NULL AND released_at IS NULL"
)

LOOP_SILENT: Final = Gauge(
    "nexovue_scheduler_loop_silent_seconds", "Seconds since this process's loop last beat."
)
LEADING: Final = Gauge(
    "nexovue_scheduler_beat_leading", "1 while this beat holds the leader lock, else 0."
)
JOBS_ADDED: Final = Counter("nexovue_scheduler_jobs_added", "Jobs beat added to the queue.")
ENQUEUE_FAILURES: Final = Counter(
    "nexovue_scheduler_enqueue_failures", "Times beat could not add what was due."
)
JOBS_ENDED: Final = Counter(
    "nexovue_scheduler_jobs_ended", "Jobs this worker ran, by how they ended.", ["outcome"]
)
JOBS_READY: Final = Gauge(
    "nexovue_scheduler_jobs_ready", "Jobs due and unclaimed, per queue (leader only).", ["queue"]
)
OLDEST_READY: Final = Gauge(
    "nexovue_scheduler_oldest_ready_job_seconds",
    "How long the oldest ready job has waited, per queue (leader only).",
    ["queue"],
)
ALERTS_WAITING: Final = Gauge(
    "nexovue_scheduler_alerts_waiting", "Alert notifications due and undelivered (leader only)."
)
OLDEST_ALERT: Final = Gauge(
    "nexovue_scheduler_oldest_alert_waiting_seconds",
    "How long the oldest undelivered alert has been due (leader only).",
)
ERASURES_OVERDUE: Final = Gauge(
    "nexovue_scheduler_erasures_overdue",
    "Deleted organizations whose erasure is past its three days (leader only).",
)
DETECTION_SETTLED_AGE: Final = Gauge(
    "nexovue_scheduler_detection_settled_minute_age_seconds",
    "How old the newest minute detection has settled is; +Inf before any (leader only).",
)
DETECTION_PROJECTS_BEHIND: Final = Gauge(
    "nexovue_scheduler_detection_projects_behind",
    "Projects detection should judge whose job has completed no run for 3 minutes (leader only).",
)
DETECTION_OLDEST_LAG: Final = Gauge(
    "nexovue_scheduler_detection_oldest_lag_seconds",
    "How long the project longest without a completed detection run has waited (leader only).",
)
DETECTION_EVENTS_RECENT: Final = Gauge(
    "nexovue_scheduler_detection_events_recent",
    "detection_events rows of each kind recorded in the last 15 minutes (leader only).",
    ["kind"],
)

CONNECTIONS_ACTIVE: Final = Gauge(
    "nexovue_scheduler_connections_active",
    "1 while a tracker connection is live and connections are on, else 0 (leader only). "
    "Beat's, so a missing scheduler-connections can be told from one left out (F22).",
)

FINDINGS_UNEXPLAINED: Final = Gauge(
    "nexovue_scheduler_findings_unexplained",
    "Open findings over 15 minutes old with no explanation version (leader only).",
)
EVIDENCE_READS_FAILED: Final = Gauge(
    "nexovue_scheduler_evidence_reads_failed_recent",
    "Evidence reads that could not complete in versions read in the last 15 minutes (leader only).",
)
EVIDENCE_TRUNCATED: Final = Gauge(
    "nexovue_scheduler_evidence_truncated_recent",
    "Versions read in the last 15 minutes that cut facts at a bound (leader only).",
)
AI_CONNECTIONS_ENABLED: Final = Gauge(
    "nexovue_scheduler_ai_connections_enabled",
    "AI connections switched on, across organizations (leader only). Beat's, so a missing "
    "scheduler-insight can be told from one left out (F3).",
)
AI_RUNS_RECENT: Final = Gauge(
    "nexovue_scheduler_ai_runs_recent",
    "AI runs started in the last 15 minutes, by purpose and outcome (leader only).",
    ["purpose", "outcome"],
)
AI_TOKENS_MONTH: Final = Gauge(
    "nexovue_scheduler_ai_tokens_month",
    "Tokens AI runs used this month, across organizations (leader only).",
)
FIX_CHECKS_REFUSED: Final = Gauge(
    "nexovue_scheduler_fix_checks_refused_recent",
    "Suggested fixes Nexovue's rules refused again on the same files in the last hour "
    "(leader only): F5's tamper signal.",
)
FIX_ROWS_RECENT: Final = Gauge(
    "nexovue_scheduler_fix_rows_recent",
    "Suggested fixes whose state moved in the last hour, by kind, state and reason (leader only).",
    ["kind", "state", "reason"],
)
FIX_OLDEST_IN_FLIGHT: Final = Gauge(
    "nexovue_scheduler_fix_oldest_in_flight_seconds",
    "How long the oldest suggested fix a job is still working on has been in its state "
    "(leader only).",
)
FIX_SWEEP_AGE: Final = Gauge(
    "nexovue_scheduler_fix_sweep_age_seconds",
    "Seconds since rung 3's suggested fix sweep last finished a pass; +Inf before any, 0 while "
    "connections are off (leader only).",
)
SLO_TARGETS_LATE: Final = Gauge(
    "nexovue_scheduler_slo_targets_late",
    "Live reliability targets last judged over 15 minutes ago (leader only).",
)
SLO_OLDEST_STATUS_AGE: Final = Gauge(
    "nexovue_scheduler_slo_oldest_status_age_seconds",
    "How old the stalest reliability target's last judging is, by maximum (leader only).",
)
NOTIFICATIONS_WITHHELD_HELD: Final = Gauge(
    "nexovue_scheduler_notifications_withheld_held",
    "Notifications held back for the same news or a snooze and not yet released (leader only).",
)

# How a worker's job ended — the label values JOBS_ENDED takes.
FINISHED: Final = "finished"
RETRIED: Final = "retried"
KILLED: Final = "killed"


@dataclass(frozen=True, slots=True)
class QueueReadings:
    jobs_ready: dict[str, int] = field(default_factory=dict)
    oldest_ready_seconds: dict[str, float] = field(default_factory=dict)
    alerts_waiting: int = 0
    oldest_alert_waiting_seconds: float = 0.0
    erasures_overdue: int = 0
    detection_settled_minute_age_seconds: float = math.inf
    detection_projects_behind: int = 0
    detection_oldest_lag_seconds: float = 0.0
    detection_events_recent: dict[str, int] = field(default_factory=dict)
    connections_active: int = 0
    findings_unexplained: int = 0
    evidence_reads_failed: int = 0
    evidence_truncated: int = 0
    ai_connections_enabled: int = 0
    ai_runs_recent: dict[tuple[str, str], int] = field(default_factory=dict)
    ai_tokens_month: int = 0
    fix_checks_refused: int = 0
    fix_rows_recent: dict[tuple[str, str, str], int] = field(default_factory=dict)
    fix_oldest_in_flight_seconds: float = 0.0
    fix_sweep_age_seconds: float = 0.0
    slo_targets_late: int = 0
    slo_oldest_status_age_seconds: float = 0.0
    notifications_withheld_held: int = 0


async def connections_live(connection: AsyncConnection, *, enabled: bool) -> bool:
    """Whether a tracker connection is live and connections are on. Beat reads it for
    `connections_active` and to decide whether `sweep_connections` is scheduled; with
    connections turned off nothing is expected to run, whatever rows remain (AC118)."""
    return enabled and bool(
        await connection.scalar(_CONNECTIONS_ACTIVE, {"kinds": list(TRACKER_KINDS)})
    )


async def ai_connections_on(connection: AsyncConnection) -> bool:
    """Whether any organization has AI wording switched on: beat schedules the AI role's
    sweep only then, so an install without the AI container queues nothing for it."""
    return bool(await connection.scalar(_AI_ON))


async def read_queues(
    connection: AsyncConnection, *, now: datetime, connections_enabled: bool = True
) -> QueueReadings:
    jobs = (await connection.execute(_JOBS_READY)).all()
    alerts = (await connection.execute(_ALERTS_WAITING)).one()
    overdue = await owed.overdue(connection, now=now)
    projects = (
        await connection.execute(_DETECTION_PROJECTS, {"not_running": NOT_RUNNING_AFTER})
    ).one()
    settled_age = await connection.scalar(_DETECTION_SETTLED_AGE)
    events = await connection.execute(
        _DETECTION_EVENTS, {"recent_seconds": DETECTION_EVENTS_RECENT_SECONDS}
    )
    recent = dict.fromkeys(DETECTION_EVENT_KINDS, 0) | {row.kind: int(row.recent) for row in events}
    connected = await connections_live(connection, enabled=connections_enabled)
    f3 = (
        await connection.execute(
            _F3_READINGS,
            {"unexplained_after": UNEXPLAINED_AFTER_SECONDS, "recent": F3_RECENT_SECONDS},
        )
    ).one()
    runs = await connection.execute(_AI_RUNS_RECENT, {"recent": F3_RECENT_SECONDS})
    refused_again = await connection.scalar(
        _FIX_CHECKS_REFUSED, {"recent": F5_REFUSED_AGAIN_SECONDS}
    )
    fix_rows = await connection.execute(
        _FIX_ROWS_RECENT, {"recent": F5_RECENT_SECONDS, "no_reason": NO_REASON}
    )
    oldest_fix = await connection.scalar(
        _FIX_OLDEST_IN_FLIGHT, {"states": list(FIX_WORKING_STATES)}
    )
    sweep_age = await _fix_sweep_age(connection, connections_enabled=connections_enabled)
    slo = (
        await connection.execute(_SLO_STATUS, {"late_after": SLO_STALE_AFTER.total_seconds()})
    ).one()
    withheld = await connection.scalar(_SLO_WITHHELD)
    return QueueReadings(
        jobs_ready={row.queue: int(row.ready) for row in jobs},
        oldest_ready_seconds={row.queue: float(row.oldest) for row in jobs},
        alerts_waiting=int(alerts.waiting),
        oldest_alert_waiting_seconds=float(alerts.oldest),
        erasures_overdue=overdue.count,
        detection_settled_minute_age_seconds=(
            math.inf if settled_age is None else float(settled_age)
        ),
        detection_projects_behind=int(projects.behind),
        detection_oldest_lag_seconds=float(projects.oldest),
        detection_events_recent=recent,
        connections_active=int(connected),
        findings_unexplained=int(f3.unexplained),
        evidence_reads_failed=int(f3.reads_failed),
        evidence_truncated=int(f3.truncated),
        ai_connections_enabled=int(f3.connections_enabled),
        ai_runs_recent={(row.purpose, row.outcome): int(row.runs) for row in runs},
        ai_tokens_month=int(f3.tokens_month),
        fix_checks_refused=int(refused_again or 0),
        fix_rows_recent={(row.kind, row.state, row.reason): int(row.rows) for row in fix_rows},
        fix_oldest_in_flight_seconds=float(oldest_fix or 0),
        fix_sweep_age_seconds=sweep_age,
        slo_targets_late=int(slo.late),
        slo_oldest_status_age_seconds=float(slo.oldest),
        notifications_withheld_held=int(withheld or 0),
    )


async def _fix_sweep_age(connection: AsyncConnection, *, connections_enabled: bool) -> float:
    """Beat schedules `sweep_fixes` only while connections are on, so with them off nothing
    is late."""
    if not connections_enabled:
        return 0.0
    age = await connection.scalar(_FIX_SWEEP_AGE, {"sweep": FIX_SWEEP_NAME})
    return math.inf if age is None else float(age)


def publish(readings: QueueReadings) -> None:
    # Cleared first: a queue that has emptied stops being reported, rather
    # than repeating its last count.
    for gauge in (
        JOBS_READY,
        OLDEST_READY,
        DETECTION_EVENTS_RECENT,
        AI_RUNS_RECENT,
        FIX_ROWS_RECENT,
    ):
        gauge.clear()
    for queue, ready in readings.jobs_ready.items():
        JOBS_READY.labels(queue=queue).set(ready)
        OLDEST_READY.labels(queue=queue).set(readings.oldest_ready_seconds[queue])
    ALERTS_WAITING.set(readings.alerts_waiting)
    OLDEST_ALERT.set(readings.oldest_alert_waiting_seconds)
    ERASURES_OVERDUE.set(readings.erasures_overdue)
    DETECTION_SETTLED_AGE.set(readings.detection_settled_minute_age_seconds)
    DETECTION_PROJECTS_BEHIND.set(readings.detection_projects_behind)
    DETECTION_OLDEST_LAG.set(readings.detection_oldest_lag_seconds)
    for kind, recent in readings.detection_events_recent.items():
        DETECTION_EVENTS_RECENT.labels(kind=kind).set(recent)
    CONNECTIONS_ACTIVE.set(readings.connections_active)
    FINDINGS_UNEXPLAINED.set(readings.findings_unexplained)
    EVIDENCE_READS_FAILED.set(readings.evidence_reads_failed)
    EVIDENCE_TRUNCATED.set(readings.evidence_truncated)
    AI_CONNECTIONS_ENABLED.set(readings.ai_connections_enabled)
    for (purpose, outcome), runs in readings.ai_runs_recent.items():
        AI_RUNS_RECENT.labels(purpose=purpose, outcome=outcome).set(runs)
    AI_TOKENS_MONTH.set(readings.ai_tokens_month)
    FIX_CHECKS_REFUSED.set(readings.fix_checks_refused)
    for (kind, state, reason), rows in readings.fix_rows_recent.items():
        FIX_ROWS_RECENT.labels(kind=kind, state=state, reason=reason).set(rows)
    FIX_OLDEST_IN_FLIGHT.set(readings.fix_oldest_in_flight_seconds)
    FIX_SWEEP_AGE.set(readings.fix_sweep_age_seconds)
    SLO_TARGETS_LATE.set(readings.slo_targets_late)
    SLO_OLDEST_STATUS_AGE.set(readings.slo_oldest_status_age_seconds)
    NOTIFICATIONS_WITHHELD_HELD.set(readings.notifications_withheld_held)


def forget_queues() -> None:
    """A beat that stops leading stops speaking for the queues."""
    for gauge in (
        JOBS_READY,
        OLDEST_READY,
        DETECTION_EVENTS_RECENT,
        AI_RUNS_RECENT,
        FIX_ROWS_RECENT,
    ):
        gauge.clear()
    for single in (
        ALERTS_WAITING,
        OLDEST_ALERT,
        ERASURES_OVERDUE,
        DETECTION_SETTLED_AGE,
        DETECTION_PROJECTS_BEHIND,
        DETECTION_OLDEST_LAG,
        CONNECTIONS_ACTIVE,
        FINDINGS_UNEXPLAINED,
        EVIDENCE_READS_FAILED,
        EVIDENCE_TRUNCATED,
        AI_CONNECTIONS_ENABLED,
        AI_TOKENS_MONTH,
        FIX_CHECKS_REFUSED,
        FIX_OLDEST_IN_FLIGHT,
        FIX_SWEEP_AGE,
        SLO_TARGETS_LATE,
        SLO_OLDEST_STATUS_AGE,
        NOTIFICATIONS_WITHHELD_HELD,
    ):
        single.set(0)
