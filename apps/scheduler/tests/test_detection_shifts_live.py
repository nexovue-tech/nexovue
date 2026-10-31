"""A person's word on a finding, and a week of nobody's, against real Postgres and
ClickHouse (plan T5.2; spec FR20, FR21, AC15, AC16, AC32, AC44, D22, D36).

The person's actions are written as the API writes them (`findings/service.py`):
the finding closed, and for a new normal a `baseline_shifts` row. What is proved is
what the project job and the baseline job make of them: a false alarm's signal
held until it reads normal; a new normal judged at once, at the new level; a
finding open a week adopted, telling the owners once and recorded as the system;
and the findings D22 never adopts. This suite fails rather than skips when the
stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import math
import socket
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from alert_fixtures import AlertTenant, rows
from clickhouse_connect.driver.client import Client
from detection_history import (
    CLICKHOUSE_DSN,
    MINUTE,
    ONSET,
    POSTGRES_DSN,
    SERVICE,
    baselines_for,
    findings,
    judge_through,
    seed,
    seed_four_weeks,
    span,
    state,
)
from live import require_stack
from nexovue_platform.detection import ADOPTED_AUDIT_ACTION, SHIFT_READINGS_NEEDED
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection.method import LOG_EDGES, RECOVERY_MINUTES
from nexovue_scheduler.detection.project_job import judge_project
from nexovue_scheduler.detection.store import DetectionStore

# Three buckets slower is about 1.68 times: past the 1.5 minimum, far short of critical.
LEVEL: Final = 3
LEVEL_RATIO: Final = math.exp(LEVEL * LOG_EDGES[1])
# Seven buckets more is about 3.4 times that level: AC16's rise past the new normal.
RISE: Final = 7
WEEK: Final = timedelta(days=7)
_SLOWER: Final[dict[str, Any]] = {
    "signal": "slower",
    "normal": 110.0,
    "current": 185.0,
    "worst": 190.0,
    "comparison": "Tuesdays around 12:00, last 4 weeks",
    "endpoint": None,
}
_FAILING: Final[dict[str, Any]] = {**_SLOWER, "signal": "failing", "normal": 0.4, "current": 3.0}


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(POSTGRES_DSN, 5432),
    suite="apps/scheduler/tests/test_detection_shifts_live.py",
    remedy="ClickHouse or Postgres is not reachable — run `just up`",
)


# ----------------------------------------------------------------- helpers


def _slow_from(
    client: Client, tenant: AlertTenant, first: datetime, last: datetime, slower_by: int
) -> None:
    minutes = span(first, last)
    seed(client, tenant, minutes, slower_by=dict.fromkeys(minutes, slower_by))


async def _execute(engine: AsyncEngine, sql: str, **parameters: Any) -> Any:
    async with engine.begin() as connection:
        return await connection.execute(text(sql), parameters)


async def _open_week_old(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    severity: str = "warning",
    signals: Sequence[dict[str, Any]] = (_SLOWER,),
    muted: bool = False,
) -> uuid.UUID:
    """A finding open for a week and an hour, as the project job leaves one."""
    result = await _execute(
        engine,
        """
        INSERT INTO findings
            (org_id, project_id, scope, service_name, severity, muted, title, signals,
             started_at)
        VALUES (:org_id, :project_id, 'service', :service, :severity, :muted,
                'checkout is 1.7 times slower than normal since 11:00 UTC',
                CAST(:signals AS jsonb), :started_at)
        RETURNING id
        """,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        service=SERVICE,
        severity=severity,
        muted=muted,
        signals=json.dumps(list(signals)),
        started_at=ONSET - WEEK - timedelta(hours=1),
    )
    finding_id: uuid.UUID = result.scalar_one()
    return finding_id


async def _shifts(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT signal, ratio, reason, from_minute FROM baseline_shifts WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )


async def _notifications(engine: AsyncEngine, tenant: AlertTenant) -> list[Any]:
    return await rows(
        engine,
        "SELECT kind, channel_id FROM notifications "
        "WHERE org_id = :org_id AND finding_id IS NOT NULL ORDER BY created_at, kind",
        org_id=tenant.org_id,
    )


async def _readings(client: Client, tenant: AlertTenant, after: datetime) -> list[Any]:
    """The newest reading of each judged minute after `after`, oldest first."""
    result = client.query(
        """
        SELECT minute, argMax(tuple(slower_verdict, slower_mode), version) AS latest
        FROM detection_readings
        WHERE tenant_id = {tenant:String} AND project_id = {project:String}
          AND minute > {after:DateTime}
        GROUP BY minute ORDER BY minute
        """,
        parameters={
            "tenant": str(tenant.org_id),
            "project": str(tenant.project_id),
            "after": after,
        },
    )
    return list(result.result_rows)


# -------------------------------------------------------------- false alarm


async def test_a_false_alarm_s_signal_reopens_nothing_until_it_reads_normal_for_recovery(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """FR20, AC32: a person's false alarm closes the finding. While the service stays
    as it was, nothing opens again; once it has read normal for *recovery*, it can."""
    seed_four_weeks(administrator, project)
    await baselines_for(engine, store, project)
    seed(administrator, project, span(ONSET - timedelta(minutes=30), ONSET))
    _slow_from(administrator, project, ONSET, ONSET + timedelta(minutes=40), slower_by=5)
    seed(administrator, project, span(ONSET + timedelta(minutes=41), ONSET + timedelta(minutes=80)))
    await judge_through(
        engine, store, project, span(ONSET - timedelta(minutes=5), ONSET + 10 * MINUTE)
    )
    [opened] = await findings(engine, project)
    assert opened.state == "open"

    await _execute(
        engine,
        """
        UPDATE findings SET state = 'closed', closed_reason = 'false_alarm',
               closed_at = :at, false_alarm_at = :at
        WHERE org_id = :org_id AND id = :id
        """,
        org_id=project.org_id,
        id=opened.id,
        at=ONSET + 10 * MINUTE,
    )
    still_slow = await judge_through(
        engine, store, project, span(ONSET + 11 * MINUTE, ONSET + 40 * MINUTE)
    )

    assert sum(outcome.opened for outcome in still_slow) == 0
    assert (await state(engine, project)).signals["held"] == {"slower": 0}

    recovered = span(ONSET + 41 * MINUTE, ONSET + (45 + RECOVERY_MINUTES) * MINUTE)
    await judge_through(engine, store, project, recovered)
    assert "held" not in (await state(engine, project)).signals
    assert len(await findings(engine, project)) == 1


# --------------------------------------------------------------- new normal


async def test_a_person_s_new_normal_judges_the_same_latency_as_normal_at_once(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC15, FR21: after *This is the new normal*, the same latency opens nothing. The
    normal built before the shift is never judged by again; the rebuilt one keeps the
    weekly pattern, so judging goes on with no learning period."""
    seed_four_weeks(administrator, project)
    await baselines_for(engine, store, project)
    seed(administrator, project, span(ONSET - timedelta(minutes=30), ONSET - MINUTE))
    _slow_from(administrator, project, ONSET, ONSET + timedelta(minutes=90), slower_by=LEVEL)
    await judge_through(
        engine, store, project, span(ONSET - timedelta(minutes=5), ONSET + 20 * MINUTE)
    )
    [opened] = await findings(engine, project)
    assert opened.state == "open"

    await _execute(
        engine,
        """
        INSERT INTO baseline_shifts
            (org_id, project_id, service_name, signal, from_minute, ratio, reason)
        VALUES (:org_id, :project_id, :service, 'slower', :from_minute, :ratio, 'new_normal')
        """,
        org_id=project.org_id,
        project_id=project.project_id,
        service=SERVICE,
        from_minute=opened.started_at,
        ratio=LEVEL_RATIO,
    )
    await _execute(
        engine,
        "UPDATE findings SET state = 'closed', closed_reason = 'new_normal', closed_at = :at "
        "WHERE org_id = :org_id AND id = :id",
        org_id=project.org_id,
        id=opened.id,
        at=ONSET + 20 * MINUTE,
    )

    stale = await judge_project(
        engine, store, project.project_id, settled=ONSET + 21 * MINUTE, now=ONSET + 21 * MINUTE
    )
    assert stale.opened == 0
    assert stale.baselines_missing, "the normal from before the shift was judged by"

    await baselines_for(engine, store, project, now=ONSET + 21 * MINUTE)
    after = await judge_through(
        engine, store, project, span(ONSET + 22 * MINUTE, ONSET + 60 * MINUTE)
    )

    judged = await _readings(administrator, project, ONSET + 21 * MINUTE)
    assert sum(outcome.opened for outcome in after) == 0
    assert judged, "nothing was judged after the new normal"
    assert {verdict for _, (verdict, _) in judged} <= {"normal", "elevated"}
    assert {mode for _, (_, mode) in judged} == {"weekly"}


# ----------------------------------------------------------------- adoption


async def test_a_finding_nobody_answers_for_a_week_is_adopted_and_tells_the_owners_once(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """AC16, AC44, D22: a service 1.7 times slower for a week becomes its own normal.
    The owners are emailed once, outside any rule; the adoption is recorded as the
    system; and a rise past the new normal opens a new finding."""
    seed_four_weeks(administrator, project)
    await baselines_for(engine, store, project)
    _slow_from(
        administrator, project, ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30), LEVEL
    )
    finding_id = await _open_week_old(engine, project)

    outcomes = await judge_through(
        engine, store, project, span(ONSET, ONSET + (SHIFT_READINGS_NEEDED + 4) * MINUTE)
    )

    [adopted] = await findings(engine, project)
    assert (adopted.id, adopted.state, adopted.closed_reason) == (finding_id, "closed", "adopted")
    assert sum(outcome.closed for outcome in outcomes) == 1
    [shift] = await _shifts(engine, project)
    assert (shift.signal, shift.reason) == ("slower", "adopted")
    assert shift.ratio == pytest.approx(LEVEL_RATIO, rel=0.1)
    assert shift.from_minute == adopted.started_at
    assert [(row.kind, row.channel_id) for row in await _notifications(engine, project)] == [
        ("adopted", project.owners_channel_id)
    ]
    [entry] = await rows(
        engine,
        "SELECT actor_user_id, action, target, detail FROM audit_log "
        "WHERE org_id = :org_id AND action = :action",
        org_id=project.org_id,
        action=ADOPTED_AUDIT_ACTION,
    )
    assert entry.actor_user_id is None
    assert entry.target == f"finding:{finding_id}"
    assert entry.detail["shifts"][0]["signal"] == "slower"

    # Then three times the new normal is a new problem (AC16).
    rebuilt_at = ONSET + timedelta(minutes=31)
    await baselines_for(engine, store, project, now=rebuilt_at)
    _slow_from(administrator, project, rebuilt_at, rebuilt_at + timedelta(minutes=20), LEVEL + RISE)
    rise = await judge_through(engine, store, project, span(rebuilt_at, rebuilt_at + 15 * MINUTE))

    assert sum(outcome.opened for outcome in rise) == 1
    newest = (await findings(engine, project))[-1]
    assert newest.id != finding_id and newest.state == "open"


@pytest.mark.parametrize(
    ("severity", "signals"),
    [("critical", (_SLOWER,)), ("warning", (_SLOWER, _FAILING))],
    ids=["critical", "a-rise-in-failures"],
)
async def test_a_critical_finding_or_a_rise_in_failures_is_never_adopted(
    engine: AsyncEngine,
    store: DetectionStore,
    administrator: Client,
    project: AlertTenant,
    severity: str,
    signals: tuple[dict[str, Any], ...],
) -> None:
    """D22, AC44."""
    seed_four_weeks(administrator, project)
    await baselines_for(engine, store, project)
    _slow_from(
        administrator, project, ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30), LEVEL
    )
    await _open_week_old(engine, project, severity=severity, signals=signals)

    await judge_through(
        engine, store, project, span(ONSET, ONSET + (SHIFT_READINGS_NEEDED + 4) * MINUTE)
    )

    [kept] = await findings(engine, project)
    assert kept.closed_reason != "adopted"
    assert await _shifts(engine, project) == []
    assert await _notifications(engine, project) == []


async def test_a_muted_finding_is_adopted_without_a_word(
    engine: AsyncEngine, store: DetectionStore, administrator: Client, project: AlertTenant
) -> None:
    """D36, BR9: a muted finding's adoption sends nothing, not even to the owners."""
    seed_four_weeks(administrator, project)
    await baselines_for(engine, store, project)
    _slow_from(
        administrator, project, ONSET - timedelta(minutes=10), ONSET + timedelta(minutes=30), LEVEL
    )
    await _open_week_old(engine, project, muted=True)

    await judge_through(
        engine, store, project, span(ONSET, ONSET + (SHIFT_READINGS_NEEDED + 4) * MINUTE)
    )

    [adopted] = await findings(engine, project)
    assert adopted.closed_reason == "adopted"
    assert await _notifications(engine, project) == []
