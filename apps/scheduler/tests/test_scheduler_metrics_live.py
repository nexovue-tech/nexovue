"""What beat reports about the queues, against real Postgres (readiness blocker 7).

A due job, an alert waiting to be delivered and an erasure owed past three days
are written in one transaction, read there, and rolled back — so the dev stack's
own workers never see them, and the readings move by exactly what was written.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from alert_fixtures import AlertTenant
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler import metrics
from nexovue_scheduler.erasure import owed

WAITED = timedelta(minutes=10)
# The database's clock and this one's are not the same clock.
CLOCKS_APART = timedelta(seconds=5)


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
