"""Every way a burn ends because a person changed something (plan T4.10; AC70, AC71).

A rule narrowed by `slo_ids`, a target disabled, deleted or re-pointed, and a deleted
check each close the target's open burn and remove every rule's position on it,
`alert_evaluations` included. Machines that were sent its opening are told it closed;
people are not, because a person chose it (design, *Ending a burn for other reasons*).

Rows are written inside one outer transaction per test and rolled back afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from nexovue_platform.alerts.model import SLO_RULE_NAME
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _call, _team, db  # noqa: F401  shared fixtures

from nexovue_api.alerts import service as alerts
from nexovue_api.alerts.schemas import UpdateRuleRequest
from nexovue_api.data.postgres.models import (
    AlertEvaluation,
    AlertRule,
    Check,
    Incident,
    ServiceLastSeen,
    SloTarget,
)


async def _target(db: AsyncSession, team: Team, **fields: Any) -> SloTarget:  # noqa: F811
    values: dict[str, Any] = {
        "org_id": team.org_id,
        "project_id": team.project_id,
        "name": f"target {uuid.uuid4()}",
        "kind": "service_availability",
        "service_name": "checkout",
        "objective_percent": Decimal("99.9"),
        "window_days": 28,
        "upstream_sampling": "no",
        "definition_key": str(uuid.uuid4()),
    } | fields
    target = SloTarget(**values)
    db.add(target)
    await db.flush()
    return target


async def _team_reporting(db: AsyncSession, name: str) -> Team:  # noqa: F811
    """A team whose project has heard from `checkout` and `cart`."""
    team = await _team(db, name)
    for service in ("checkout", "cart"):
        db.add(
            ServiceLastSeen(
                org_id=team.org_id,
                project_id=team.project_id,
                service_name=service,
                last_seen_at=datetime.now(UTC),
            )
        )
    await db.flush()
    return team


async def _default_rule(db: AsyncSession, team: Team) -> AlertRule:  # noqa: F811
    rule = await db.scalar(
        select(AlertRule).where(
            AlertRule.project_id == team.project_id, AlertRule.name == SLO_RULE_NAME
        )
    )
    assert rule is not None
    return rule


async def _burning(db: AsyncSession, team: Team, target: SloTarget) -> Incident:  # noqa: F811
    """An open burn under the default rule, and the position beside it."""
    rule = await _default_rule(db, team)
    now = datetime.now(UTC)
    key = f"slo:{target.id}"
    incident = Incident(
        org_id=team.org_id,
        project_id=team.project_id,
        rule_id=rule.id,
        rule_name=rule.name,
        condition_kind="slo_burn",
        subject_key=key,
        subject_label="checkout availability, 99.9% over 28 days",
        severity="critical",
        opened_at=now,
        detail={"slo_id": str(target.id)},
        # What the job writes (T5.2): a deleted check finds its burns by this, not the
        # detail's figures (founder decision 6, 2026-11-09).
        about_key=f"service:{target.service_name}"
        if target.service_name is not None
        else f"check:{target.check_id}",
        about_signal="availability",
        about_check_id=target.check_id,
    )
    db.add(incident)
    db.add(
        AlertEvaluation(
            org_id=team.org_id,
            project_id=team.project_id,
            rule_id=rule.id,
            subject_key=key,
            phase="firing",
            phase_since=now,
            evaluated_at=now,
        )
    )
    await db.flush()
    return incident


async def _ended(db: AsyncSession, incident: Incident) -> bool:  # noqa: F811
    await db.refresh(incident)
    positions = await db.scalars(
        select(AlertEvaluation).where(AlertEvaluation.subject_key == incident.subject_key)
    )
    return incident.resolved_at is not None and list(positions) == []


async def test_ac70_a_rule_narrowed_away_from_a_target_closes_its_burn(
    db: AsyncSession,  # noqa: F811
) -> None:
    team = await _team_reporting(db, "Narrowed")
    target = await _target(db, team)
    other = await _target(db, team, service_name="cart")
    incident = await _burning(db, team, target)
    rule = await _default_rule(db, team)

    await alerts.update_rule(
        db,
        org_id=team.org_id,
        actor_user_id=team.member.id,
        actor_role="member",
        rule_id=rule.id,
        request=UpdateRuleRequest(condition={"kind": "slo_burn", "slo_ids": [str(other.id)]}),
    )
    assert await _ended(db, incident)


async def test_ac70_a_disabled_target_closes_its_burn(db: AsyncSession) -> None:  # noqa: F811
    team = await _team_reporting(db, "Disabled")
    target = await _target(db, team)
    incident = await _burning(db, team, target)
    changed = await _call(db, team, team.member, "PATCH", f"/slos/{target.id}", {"enabled": False})
    assert changed.status_code == 200, changed.text
    assert await _ended(db, incident)


async def test_ac71_a_deleted_target_closes_its_burn(db: AsyncSession) -> None:  # noqa: F811
    team = await _team_reporting(db, "Deleted")
    target = await _target(db, team)
    incident = await _burning(db, team, target)
    deleted = await _call(db, team, team.member, "DELETE", f"/slos/{target.id}")
    assert deleted.status_code == 204, deleted.text
    assert await _ended(db, incident)


async def test_ac70_a_re_pointed_target_closes_its_burn(db: AsyncSession) -> None:  # noqa: F811
    team = await _team_reporting(db, "Repointed")
    target = await _target(db, team)
    incident = await _burning(db, team, target)
    changed = await _call(
        db, team, team.member, "PATCH", f"/slos/{target.id}", {"service_name": "cart"}
    )
    assert changed.status_code == 200, changed.text
    assert await _ended(db, incident)


async def test_ac70_a_renamed_target_keeps_its_burn(db: AsyncSession) -> None:  # noqa: F811
    team = await _team_reporting(db, "Renamed")
    target = await _target(db, team)
    incident = await _burning(db, team, target)
    changed = await _call(db, team, team.member, "PATCH", f"/slos/{target.id}", {"name": "new"})
    assert changed.status_code == 200, changed.text
    assert not await _ended(db, incident)


async def test_ac70_a_deleted_check_closes_its_target_s_burn_and_leaves_a_null_check(
    db: AsyncSession,  # noqa: F811
) -> None:
    team = await _team_reporting(db, "Checked")
    check = Check(
        org_id=team.org_id,
        project_id=team.project_id,
        name="Payment",
        type="tcp",
        config={"type": "tcp", "host": "example.test", "port": 443},
        interval_seconds=60,
    )
    db.add(check)
    await db.flush()
    target = await _target(
        db,
        team,
        kind="check_availability",
        service_name=None,
        upstream_sampling=None,
        check_id=check.id,
    )
    incident = await _burning(db, team, target)
    deleted = await _call(db, team, team.owner, "DELETE", f"/checks/{check.id}")
    assert deleted.status_code == 204, deleted.text
    assert await _ended(db, incident)
    await db.refresh(target)
    assert target.check_id is None
