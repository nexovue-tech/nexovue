"""What beat reports about the queues and about detection, against real Postgres
(readiness blocker 7; F6 plan T7.1).

A due job, an alert waiting to be delivered, an erasure owed past three days, a
project detection has stopped judging, the detection sweep's own record and
detection's events are written in one transaction, read there, and rolled back —
so the dev stack's own workers never see them, and the readings move by exactly
what was written.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler import metrics
from nexovue_scheduler.detection import coordinator, holds
from nexovue_scheduler.erasure import owed

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_scheduler_metrics_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

WAITED = timedelta(minutes=10)
# The database's clock and this one's are not the same clock.
CLOCKS_APART = timedelta(seconds=5)
# Detection is *not running* for a project past 3 minutes (design 2.1, item 2).
STOPPED_FOR = timedelta(minutes=10)
JUST_RAN = timedelta(minutes=1)
# Older than *detection stalled*'s 10 minutes.
STALLED_FOR = timedelta(minutes=12)

_RUN: Final = text(
    """
    INSERT INTO detection_project_runs (org_id, project_id, last_completed_at, expected_since)
    VALUES (
        :org, :project,
        now() - CAST(:completed_ago AS interval), now() - CAST(:expected_ago AS interval)
    )
    ON CONFLICT (org_id, project_id) DO UPDATE
    SET last_completed_at = EXCLUDED.last_completed_at, expected_since = EXCLUDED.expected_since
    """
)


async def test_a_due_job_and_a_waiting_alert_and_an_overdue_erasure_are_counted(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    queue = f"metrics-test-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=now)
            await connection.execute(
                text(
                    "INSERT INTO jobs (task, queue, kwargs, run_after, max_attempts, "
                    "soft_limit_seconds, hard_limit_seconds) "
                    "VALUES ('test.task', :queue, '{}', :due, 1, 5, 10)"
                ),
                {"queue": queue, "due": now - WAITED},
            )
            incident = (
                await connection.execute(
                    text(
                        "INSERT INTO incidents (org_id, project_id, rule_id, rule_name, "
                        "condition_kind, subject_key, subject_label, severity, opened_at, detail) "
                        "VALUES (:org, :project, :rule, 'Any check down', 'check_state', "
                        ":subject, 'A check', 'critical', now(), '{\"state\": \"down\"}') "
                        "RETURNING id"
                    ),
                    {
                        "org": tenant.org_id,
                        "project": tenant.project_id,
                        "rule": tenant.default_rule_id,
                        "subject": f"check:{uuid.uuid4()}",
                    },
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO notifications (org_id, project_id, incident_id, channel_id, "
                    "kind, severity, deliver_after, created_at) "
                    "VALUES (:org, :project, :incident, :channel, 'opened', 'critical', "
                    ":due, :due)"
                ),
                {
                    "org": tenant.org_id,
                    "project": tenant.project_id,
                    "incident": incident,
                    "channel": tenant.owners_channel_id,
                    "due": now - WAITED,
                },
            )
            await connection.execute(
                text("UPDATE organizations SET deleted_at = :at WHERE id = :id"),
                {"at": now - owed.OVERDUE_AFTER - timedelta(hours=1), "id": tenant.org_id},
            )

            after = await metrics.read_queues(connection, now=now)
        finally:
            await transaction.rollback()

    assert after.jobs_ready[queue] == 1
    assert after.oldest_ready_seconds[queue] >= (WAITED - CLOCKS_APART).total_seconds()
    assert after.alerts_waiting == before.alerts_waiting + 1
    assert after.oldest_alert_waiting_seconds >= (WAITED - CLOCKS_APART).total_seconds()
    assert after.erasures_overdue == before.erasures_overdue + 1


async def test_a_project_detection_has_not_judged_for_three_minutes_is_counted_behind(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """C8: install-wide, so the count and the oldest lag name no project. *Behind*
    is the API's *not running*: expected, and no run for 3 minutes."""
    keys = {"org": tenant.org_id, "project": tenant.project_id}
    a_day = timedelta(days=1)
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("DELETE FROM detection_project_runs WHERE project_id = :project"), keys
            )
            before = await metrics.read_queues(connection, now=now)
            await connection.execute(
                _RUN, keys | {"completed_ago": JUST_RAN, "expected_ago": a_day}
            )
            judged_on_time = await metrics.read_queues(connection, now=now)
            await connection.execute(
                _RUN, keys | {"completed_ago": STOPPED_FOR, "expected_ago": a_day}
            )
            stopped = await metrics.read_queues(connection, now=now)
            await connection.execute(
                text(
                    "UPDATE detection_project_runs SET expected_since = NULL "
                    "WHERE project_id = :project"
                ),
                keys,
            )
            no_longer_expected = await metrics.read_queues(connection, now=now)
        finally:
            await transaction.rollback()

    assert judged_on_time.detection_projects_behind == before.detection_projects_behind
    assert judged_on_time.detection_oldest_lag_seconds >= (JUST_RAN - CLOCKS_APART).total_seconds()
    assert stopped.detection_projects_behind == before.detection_projects_behind + 1
    assert stopped.detection_oldest_lag_seconds >= (STOPPED_FOR - CLOCKS_APART).total_seconds()
    assert no_longer_expected.detection_projects_behind == before.detection_projects_behind


async def test_the_settled_minute_the_sweep_records_is_read_as_its_age(
    engine: AsyncEngine,
) -> None:
    """*Detection stalled*'s reading. Before any sweep has settled a minute it is
    infinitely old, so a scheduler whose sweep never ran is not read as healthy."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("DELETE FROM detection_sweep"))
            never = await metrics.read_queues(connection, now=datetime.now(UTC))
            await coordinator.record_sweep(connection, None)
            settled_nothing = await metrics.read_queues(connection, now=datetime.now(UTC))
            stalled_at = await connection.scalar(
                text("SELECT date_trunc('minute', now()) - CAST(:stalled AS interval)"),
                {"stalled": STALLED_FOR},
            )
            await coordinator.record_sweep(connection, stalled_at)
            stalled = await metrics.read_queues(connection, now=datetime.now(UTC))
            # A minute held back by nexovue's intake never moves the record backwards.
            await coordinator.record_sweep(connection, stalled_at - JUST_RAN)
            await coordinator.record_sweep(connection, None)
            still = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    assert never.detection_settled_minute_age_seconds == math.inf
    assert settled_nothing.detection_settled_minute_age_seconds == math.inf
    stalled_seconds = STALLED_FOR.total_seconds()
    assert stalled_seconds <= stalled.detection_settled_minute_age_seconds < stalled_seconds + 60
    assert (
        still.detection_settled_minute_age_seconds == stalled.detection_settled_minute_age_seconds
    )


async def test_detection_events_of_the_last_fifteen_minutes_are_counted_by_kind(
    engine: AsyncEngine,
) -> None:
    """*Detection held by intake*'s reading. Beat reads the rows: the sweep that
    writes them runs in a worker's child process, whose metrics no scrape sees."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=datetime.now(UTC))
            for kind, ago in (
                (holds.HOLD_EXPIRED, timedelta(minutes=1)),
                (holds.GUARD_ENGAGED, timedelta(minutes=20)),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO detection_events (at, kind, detail) "
                        "VALUES (now() - CAST(:ago AS interval), :kind, '{}')"
                    ),
                    {"ago": ago, "kind": kind},
                )
            after = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    assert set(after.detection_events_recent) == set(holds.EVENT_KINDS)
    expired, guarded = holds.HOLD_EXPIRED, holds.GUARD_ENGAGED
    assert after.detection_events_recent[expired] == before.detection_events_recent[expired] + 1
    assert after.detection_events_recent[guarded] == before.detection_events_recent[guarded]


async def test_beat_says_whether_any_tracker_connection_is_live(engine: AsyncEngine) -> None:
    """F22 AC118, AC123: `connections_active` is what lets the monitor tell a role left out on
    purpose from one that died. It reads the rows, so it is beat's to say, and it is 0 while
    connections are turned off, or when only F5's own App is connected."""
    org_id = uuid.uuid4()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # Every live connection in this database, ended for this transaction only.
            await connection.execute(
                text(
                    "UPDATE connections SET state = 'disconnected', sealed_credential = NULL, "
                    "sealed_webhook_secret = NULL, sealed_manifest_code = NULL, "
                    "setup_state_hash = NULL WHERE state <> 'disconnected'"
                )
            )
            none = await metrics.read_queues(connection, now=datetime.now(UTC))
            await connection.execute(
                text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'M')"),
                {"id": org_id, "slug": f"metrics-{org_id.hex[:12]}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO connections (org_id, kind, state) "
                    "VALUES (:org, 'github_fix', 'working')"
                ),
                {"org": org_id},
            )
            fix_only = await metrics.read_queues(connection, now=datetime.now(UTC))
            await connection.execute(
                text(
                    "INSERT INTO connections (org_id, kind, state) "
                    "VALUES (:org, 'github', 'setting_up')"
                ),
                {"org": org_id},
            )
            one = await metrics.read_queues(connection, now=datetime.now(UTC))
            turned_off = await metrics.read_queues(
                connection, now=datetime.now(UTC), connections_enabled=False
            )
        finally:
            await transaction.rollback()

    assert (none.connections_active, fix_only.connections_active) == (0, 0)
    assert (one.connections_active, turned_off.connections_active) == (1, 0)
    metrics.publish(one)
    assert metrics.CONNECTIONS_ACTIVE._value.get() == 1


async def test_f3_s_readings_move_by_exactly_what_was_written(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """F3 T5.10: an open finding 20 minutes old with no explanation, a version with a failed
    read and a cut, an AI connection switched on, runs and the month's tokens; all counted
    across organizations, with no tenant label, and beat's switch for the AI sweep."""
    from alert_fixtures import add_finding

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("UPDATE ai_connections SET enabled = false"))
            before = await metrics.read_queues(connection, now=datetime.now(UTC))
            was_on = await metrics.ai_connections_on(connection)
            await add_finding(
                connection, tenant, started_at=datetime.now(UTC) - timedelta(minutes=20)
            )
            explained = await add_finding(
                connection, tenant, service="payments", started_at=datetime.now(UTC)
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO finding_explanations (org_id, project_id, finding_id, version,
                        fingerprint, reads_failed, evidence, aliases, summary)
                    VALUES (:org, :project, :finding, 1, repeat('a', 64), 2,
                        '{"items": [{"kind": "absence", "values": {"reason": "truncated"}}]}',
                        '{}', '{}')
                    """
                ),
                {"org": tenant.org_id, "project": tenant.project_id, "finding": explained},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_connections (org_id, wire_format, base_url, model,
                        monthly_token_limit, enabled, consented_at, consented_base_url,
                        consented_fingerprint, consented_schema)
                    VALUES (:org, 'openai_chat', 'https://p.example/v1', 'm', 10, true, now(),
                        'https://p.example/v1', repeat('a', 64), 1)
                    """
                ),
                {"org": tenant.org_id},
            )
            for outcome in ("done", "done", "invalid"):
                await connection.execute(
                    text(
                        "INSERT INTO ai_runs (org_id, purpose, wire_format, model, "
                        "provider_host, outcome) VALUES (:org, 'explain', 'openai_chat', 'm', "
                        "'https://p.example', :outcome)"
                    ),
                    {"org": tenant.org_id, "outcome": outcome},
                )
            await connection.execute(
                text(
                    "INSERT INTO ai_usage_monthly (org_id, month, used_tokens) VALUES "
                    "(:org, date_trunc('month', now() AT TIME ZONE 'UTC')::date, 700)"
                ),
                {"org": tenant.org_id},
            )
            after = await metrics.read_queues(connection, now=datetime.now(UTC))
            is_on = await metrics.ai_connections_on(connection)
        finally:
            await transaction.rollback()

    assert after.findings_unexplained - before.findings_unexplained == 1
    assert after.evidence_reads_failed - before.evidence_reads_failed == 2
    assert after.evidence_truncated - before.evidence_truncated == 1
    assert (before.ai_connections_enabled, after.ai_connections_enabled) == (0, 1)
    assert (was_on, is_on) == (False, True)
    done = ("explain", "done")
    assert after.ai_runs_recent[done] - before.ai_runs_recent.get(done, 0) == 2
    assert after.ai_tokens_month - before.ai_tokens_month == 700
    metrics.publish(after)
    assert metrics.AI_CONNECTIONS_ENABLED._value.get() == 1


async def test_f5_s_tamper_signal_counts_refusals_again_in_the_last_hour_only(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """F5 T5.1 (D18, ADR-096): a suggestion the rules refused again on the same files in the
    last hour counts; an older one, and one merely out of date, do not."""
    from alert_fixtures import add_finding

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=datetime.now(UTC))
            for minutes, state, reason in (
                (10, "failed", "checks_refused_at_click"),
                (90, "failed", "checks_refused_at_click"),
                (5, "stale", "rules_changed"),
            ):
                finding = await add_finding(
                    connection,
                    tenant,
                    service=f"svc-{minutes}",
                    started_at=datetime.now(UTC) - timedelta(hours=3),
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, state,
                            reason, state_changed_at)
                        VALUES (:org, :project, :finding, 'source', :state, :reason,
                            now() - make_interval(mins => :minutes))
                        """
                    ),
                    {
                        "org": tenant.org_id,
                        "project": tenant.project_id,
                        "finding": finding,
                        "state": state,
                        "reason": reason,
                        "minutes": minutes,
                    },
                )
            after = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    assert after.fix_checks_refused - before.fix_checks_refused == 1
    metrics.publish(after)
    assert metrics.FIX_CHECKS_REFUSED._value.get() == after.fix_checks_refused


_SWEEP_FINISHED: Final = text(
    """
    INSERT INTO fix_sweep_status (sweep, finished_at)
    VALUES ('sweep_fixes', now() - make_interval(mins => :minutes))
    ON CONFLICT (sweep) DO UPDATE SET finished_at = EXCLUDED.finished_at
    """
)
SWEEP_AGE_MINUTES: Final = 20


async def test_f5_s_sweep_age_is_read_while_connections_are_on(engine: AsyncEngine) -> None:
    """F5 T9.1 (open decision 70): how long since `sweep_fixes` last finished a whole pass;
    never finished reads +Inf; with connections off nothing is expected, so 0."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("DELETE FROM fix_sweep_status"))
            never = await metrics.read_queues(connection, now=datetime.now(UTC))
            await connection.execute(_SWEEP_FINISHED, {"minutes": SWEEP_AGE_MINUTES})
            on = await metrics.read_queues(connection, now=datetime.now(UTC))
            off = await metrics.read_queues(
                connection, now=datetime.now(UTC), connections_enabled=False
            )
        finally:
            await transaction.rollback()

    assert never.fix_sweep_age_seconds == math.inf
    expected = SWEEP_AGE_MINUTES * 60
    assert abs(on.fix_sweep_age_seconds - expected) < CLOCKS_APART.total_seconds()
    assert off.fix_sweep_age_seconds == 0
    metrics.publish(on)
    assert metrics.FIX_SWEEP_AGE._value.get() == on.fix_sweep_age_seconds


async def test_f5_s_rows_by_state_and_reason_and_the_oldest_in_flight(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """F5 T9.1 (design, *Observability*: "rows by state and reason in the last hour, the oldest
    row in flight"): a row counts once under its kind, state and reason when its state moved
    in the last hour; an older move does not; the oldest working row's age is read."""
    from alert_fixtures import add_finding

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await metrics.read_queues(connection, now=datetime.now(UTC))
            for minutes, kind, state, reason in (
                (10, "source", "refused", "withheld_K7"),
                (20, "source", "failed", "timed_out"),
                (90, "source", "refused", "withheld_K7"),
                (45, "source", "proposing", None),
            ):
                finding = await add_finding(
                    connection,
                    tenant,
                    service=f"svc-{minutes}-{kind}",
                    started_at=datetime.now(UTC) - timedelta(hours=3),
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, state,
                            reason, state_changed_at)
                        VALUES (:org, :project, :finding, :kind, :state, :reason,
                            now() - make_interval(mins => :minutes))
                        """
                    ),
                    {
                        "org": tenant.org_id,
                        "project": tenant.project_id,
                        "finding": finding,
                        "kind": kind,
                        "state": state,
                        "reason": reason,
                        "minutes": minutes,
                    },
                )
            after = await metrics.read_queues(connection, now=datetime.now(UTC))
        finally:
            await transaction.rollback()

    def moved(key: tuple[str, str, str]) -> int:
        return after.fix_rows_recent.get(key, 0) - before.fix_rows_recent.get(key, 0)

    assert moved(("source", "refused", "withheld_K7")) == 1
    assert moved(("source", "failed", "timed_out")) == 1
    assert moved(("source", "proposing", metrics.NO_REASON)) == 1
    assert after.fix_oldest_in_flight_seconds >= 45 * 60 - CLOCKS_APART.total_seconds()
    metrics.publish(after)
    assert (
        metrics.FIX_ROWS_RECENT.labels(
            kind="source", state="refused", reason="withheld_K7"
        )._value.get()
        == after.fix_rows_recent[("source", "refused", "withheld_K7")]
    )
