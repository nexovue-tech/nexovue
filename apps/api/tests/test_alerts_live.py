"""Alert rules and incidents through the API's service, against real Postgres
(F21: FR5, FR8, FR14, FR22, FR24, FR25, FR26; AC22, AC26). Rolled back after each test.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.alerts import service
from nexovue_api.alerts.schemas import UpdateRuleRequest, WriteRuleRequest
from nexovue_api.checks import service as checks
from nexovue_api.data.postgres.models import (
    AlertEvaluation,
    AuditLogEntry,
    Check,
    Incident,
    Notification,
    NotificationChannel,
)
from nexovue_api.errors.exceptions import InvalidRequestError, NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
DOWN: Final = {"kind": "check_state", "states": {"down": "critical"}}


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_alerts_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Rules",
        email=f"rules-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


async def _owners(db: AsyncSession, tenant: ProvisionedTenant) -> uuid.UUID:
    channel_id: uuid.UUID = (
        await db.scalars(
            select(NotificationChannel.id).where(
                NotificationChannel.org_id == tenant.organization.id,
                NotificationChannel.kind == "owners",
            )
        )
    ).one()
    return channel_id


async def _check(db: AsyncSession, tenant: ProvisionedTenant, name: str = "Checkout API") -> Check:
    check = Check(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        name=name,
        type="tcp",
        config={"type": "tcp", "host": "example.test", "port": 443},
        interval_seconds=60,
    )
    db.add(check)
    await db.flush()
    return check


async def _create(db: AsyncSession, tenant: ProvisionedTenant, **fields: Any) -> Any:
    request = {
        "project_id": tenant.project.id,
        "name": "Checkout",
        "condition": DOWN,
        "channel_ids": [await _owners(db, tenant)],
    } | fields
    return await service.create_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        request=WriteRuleRequest(**request),
    )


async def test_a_new_project_lists_its_default_rule_sending_to_the_owners(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    listed = await service.list_rules(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    [rule] = listed.rules
    assert (rule.name, rule.is_default, rule.notifying_nobody) == ("Any check down", True, False)
    assert [channel.kind for channel in rule.channels] == ["owners"]


async def test_a_rule_is_saved_in_the_scheduler_s_own_vocabulary(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    check = await _check(db, tenant)
    created = await _create(
        db,
        tenant,
        condition={
            "kind": "check_state",
            "check_ids": [str(check.id)],
            "states": {"degraded": "warning", "down": "critical"},
        },
    )
    assert created.condition == {
        "kind": "check_state",
        "check_ids": [str(check.id)],
        "states": {"degraded": "warning", "down": "critical"},
    }
    assert created.covers_nothing is False


@pytest.mark.parametrize(
    ("condition", "field"),
    [
        ({"kind": "check_state", "states": {}}, "condition.states"),
        ({"kind": "check_state", "states": {"up": "critical"}}, "condition.states.up.[key]"),
        ({"kind": "cpu_above", "states": {"down": "critical"}}, "condition.kind"),
        (
            {
                "kind": "check_state",
                "check_ids": [str(uuid.uuid4())],
                "states": {"down": "critical"},
            },
            "condition.check_ids",
        ),
        (
            {"kind": "check_state", "check_ids": [], "states": {"down": "critical"}},
            "condition.check_ids",
        ),
    ],
)
async def test_a_rule_that_cannot_fire_is_refused_with_its_field_named(
    db: AsyncSession, tenant: ProvisionedTenant, condition: dict[str, Any], field: str
) -> None:
    with pytest.raises(InvalidRequestError) as refused:
        await _create(db, tenant, condition=condition)
    assert field in [problem["field"] for problem in refused.value.extensions["errors"]]


async def test_a_rule_cannot_send_to_another_organizations_channel(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    other = await provision_tenant(
        db,
        organization_name="Other",
        email=f"other-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    with pytest.raises(InvalidRequestError):
        await _create(db, tenant, channel_ids=[await _owners(db, other)])


async def test_a_rule_whose_checks_are_gone_covers_nothing_and_says_so(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    check = await _check(db, tenant)
    created = await _create(
        db,
        tenant,
        condition={
            "kind": "check_state",
            "check_ids": [str(check.id)],
            "states": {"down": "critical"},
        },
    )
    await db.delete(check)
    await db.flush()
    again = await service.get_rule(db, org_id=tenant.organization.id, rule_id=created.id)
    assert again.covers_nothing is True


async def test_a_rule_naming_a_deleted_check_can_still_be_edited_and_forgets_it(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """The console cannot show a deleted check to untick, so saving the rule as it
    stands must not be refused for naming it (spec, *Error cases*: covers nothing)."""
    kept, gone = await _check(db, tenant), await _check(db, tenant, name="Search API")
    condition = {
        "kind": "check_state",
        "check_ids": [str(kept.id), str(gone.id)],
        "states": {"down": "critical"},
    }
    created = await _create(db, tenant, condition=condition)
    await db.delete(gone)
    await db.flush()

    saved = await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(name="Checkout, renamed", condition=condition),
    )
    assert saved.name == "Checkout, renamed"
    assert saved.condition["check_ids"] == [str(kept.id)]

    # Every named check gone: the ordinary "choose a check" refusal, on the checks field.
    await db.delete(kept)
    await db.flush()
    with pytest.raises(InvalidRequestError) as refused:
        await service.update_rule(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            rule_id=created.id,
            request=UpdateRuleRequest(condition=condition),
        )
    assert [problem["field"] for problem in refused.value.extensions["errors"]] == [
        "condition.check_ids"
    ]
    # A check never on the rule is still refused.
    with pytest.raises(InvalidRequestError):
        await service.update_rule(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            rule_id=created.id,
            request=UpdateRuleRequest(condition=condition | {"check_ids": [str(uuid.uuid4())]}),
        )


async def test_a_rule_left_without_channels_is_notifying_nobody(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR25."""
    created = await _create(db, tenant)
    updated = await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(channel_ids=[]),
    )
    assert updated.notifying_nobody is True


async def test_a_mute_records_who_and_until_when(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant)
    until = datetime.now(UTC) + timedelta(hours=4)
    muted = await service.mute_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        until=until,
    )
    assert (muted.muted_until, muted.muted_by) == (until, tenant.user.id)
    with pytest.raises(InvalidRequestError):
        await service.mute_rule(
            db,
            org_id=tenant.organization.id,
            actor_user_id=tenant.user.id,
            rule_id=created.id,
            until=datetime.now(UTC) - timedelta(minutes=1),
        )


async def test_deleting_a_rule_closes_its_open_incidents_and_keeps_their_history(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant)
    incident = Incident(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        rule_id=created.id,
        rule_name=created.name,
        condition_kind="check_state",
        subject_key="check:1",
        subject_label="Checkout API",
        severity="critical",
        opened_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db.add(incident)
    await db.flush()

    await service.delete_rule(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, rule_id=created.id
    )
    await db.refresh(incident)
    assert incident.resolved_at is not None
    assert (incident.rule_id, incident.rule_name) == (None, "Checkout")
    with pytest.raises(NotFoundError):
        await service.get_rule(db, org_id=tenant.organization.id, rule_id=created.id)


async def test_incidents_list_open_first_with_who_was_told(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    rule = (
        await service.list_rules(db, org_id=tenant.organization.id, project_id=tenant.project.id)
    ).rules[0]
    now = datetime.now(UTC)
    open_one = Incident(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        rule_id=rule.id,
        rule_name=rule.name,
        condition_kind="check_state",
        subject_key="check:open",
        subject_label="Checkout API",
        severity="critical",
        opened_at=now - timedelta(minutes=3),
    )
    closed_one = Incident(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        rule_id=rule.id,
        rule_name=rule.name,
        condition_kind="check_state",
        subject_key="check:closed",
        subject_label="Search API",
        severity="critical",
        opened_at=now - timedelta(hours=2),
        resolved_at=now - timedelta(hours=1),
    )
    db.add_all([open_one, closed_one])
    await db.flush()
    db.add(
        Notification(
            org_id=tenant.organization.id,
            project_id=tenant.project.id,
            incident_id=open_one.id,
            channel_id=await _owners(db, tenant),
            kind="opened",
            severity="critical",
            state="delivered",
        )
    )
    await db.flush()

    listed = await service.list_incidents(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    assert [incident.subject_label for incident in listed.open] == ["Checkout API"]
    assert [incident.subject_label for incident in listed.recent] == ["Checkout API", "Search API"]
    [told] = listed.open[0].notifications
    assert (told.channel_kind, told.kind, told.state) == ("owners", "opened", "delivered")


async def test_the_sidebar_count_is_this_project_s_open_incidents_only(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    rule_id = (await _create(db, tenant)).id
    await _open(db, tenant, rule_id, "check:critical")
    warning = await _open(db, tenant, rule_id, "check:warning")
    warning.severity = "warning"
    closed = await _open(db, tenant, rule_id, "check:closed")
    closed.resolved_at = datetime.now(UTC)
    other = await provision_tenant(
        db,
        organization_name="Elsewhere",
        email=f"elsewhere-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    await _open(db, other, (await _create(db, other)).id, "check:elsewhere")
    await db.flush()

    count = await service.open_incident_count(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )

    assert (count.open, count.critical) == (2, 1)


async def test_the_sidebar_count_refuses_another_organization_s_project(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    other = await provision_tenant(
        db,
        organization_name="Elsewhere",
        email=f"elsewhere-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    with pytest.raises(NotFoundError):
        await service.open_incident_count(
            db, org_id=tenant.organization.id, project_id=other.project.id
        )


async def test_a_check_shows_the_rules_that_cover_it_and_its_open_incident(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR24."""
    check = await _check(db, tenant)
    elsewhere = await _check(db, tenant, "Search API")
    await _create(
        db,
        tenant,
        name="Search only",
        condition={
            "kind": "check_state",
            "check_ids": [str(elsewhere.id)],
            "states": {"down": "critical"},
        },
    )
    coverage = await service.coverage_of_check(
        db, org_id=tenant.organization.id, project_id=tenant.project.id, check_id=check.id
    )
    assert [rule.name for rule in coverage.rules] == ["Any check down"]
    assert coverage.open_incidents == []


async def test_every_rule_change_is_audited(db: AsyncSession, tenant: ProvisionedTenant) -> None:
    """AC26."""
    created = await _create(db, tenant)
    await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(name="Checkout, renamed"),
    )
    await service.mute_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    await service.delete_rule(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, rule_id=created.id
    )
    actions = [
        entry.action
        for entry in await db.scalars(
            select(AuditLogEntry)
            .where(AuditLogEntry.target == f"alert_rule:{created.id}")
            .order_by(AuditLogEntry.id)
        )
    ]
    assert actions == [
        "alert_rule.created",
        "alert_rule.updated",
        "alert_rule.muted",
        "alert_rule.deleted",
    ]


async def test_service_rules_are_saved_with_their_defaults_in_the_scheduler_s_vocabulary(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR5: the waits and minimum count are filled in, as the spec's table has them."""
    created = await _create(
        db,
        tenant,
        name="Checkout errors",
        condition={"kind": "service_error_rate", "services": ["checkout"], "threshold_percent": 5},
    )
    assert created.condition == {
        "kind": "service_error_rate",
        "services": ["checkout"],
        "threshold_percent": 5.0,
        "window_minutes": 5,
        "min_requests": 20,
        "fire_after_minutes": 5,
        "resolve_after_minutes": 5,
        "severity": "critical",
    }
    with pytest.raises(InvalidRequestError):
        await _create(db, tenant, condition={"kind": "service_latency_p95", "threshold_ms": -1})
    with pytest.raises(InvalidRequestError):
        await _create(db, tenant, condition={"kind": "service_silent", "silent_minutes": 1})


async def test_a_service_shows_only_the_rules_that_watch_it(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR24, for a service's page."""
    await _create(db, tenant, name="Every service silent", condition={"kind": "service_silent"})
    await _create(
        db,
        tenant,
        name="Cart latency",
        condition={"kind": "service_latency_p95", "services": ["cart"], "threshold_ms": 500},
    )
    coverage = await service.coverage_of_service(
        db, org_id=tenant.organization.id, project_id=tenant.project.id, service="checkout"
    )
    assert [rule.name for rule in coverage.rules] == ["Every service silent"]


async def _open(
    db: AsyncSession, tenant: ProvisionedTenant, rule_id: uuid.UUID, subject: str
) -> Incident:
    """An open incident, and the threshold position a service rule keeps beside it."""
    now = datetime.now(UTC)
    incident = Incident(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        rule_id=rule_id,
        rule_name="rule",
        condition_kind="check_state",
        subject_key=subject,
        subject_label=subject,
        severity="critical",
        opened_at=now,
    )
    position = AlertEvaluation(
        org_id=tenant.organization.id,
        project_id=tenant.project.id,
        rule_id=rule_id,
        subject_key=subject,
        phase="firing",
        phase_since=now,
        evaluated_at=now,
    )
    db.add_all([incident, position])
    await db.flush()
    return incident


async def _still_open(db: AsyncSession, *incidents: Incident) -> list[bool]:
    for incident in incidents:
        await db.refresh(incident)
    return [incident.resolved_at is None for incident in incidents]


async def _positions(db: AsyncSession, rule_id: uuid.UUID) -> list[str]:
    found = await db.scalars(
        select(AlertEvaluation.subject_key).where(AlertEvaluation.rule_id == rule_id)
    )
    return sorted(found)


async def test_disabling_a_rule_closes_what_it_had_open(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """A disabled rule is never evaluated again, so nothing else would ever close it."""
    created = await _create(db, tenant)
    incident = await _open(db, tenant, created.id, f"check:{uuid.uuid4()}")

    await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(enabled=False),
    )
    assert await _still_open(db, incident) == [False]
    assert await _positions(db, created.id) == []


async def test_narrowing_a_rule_closes_only_what_it_stopped_watching(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    kept, dropped = await _check(db, tenant), await _check(db, tenant, name="Search API")
    condition = {
        "kind": "check_state",
        "check_ids": [str(kept.id), str(dropped.id)],
        "states": {"down": "critical"},
    }
    created = await _create(db, tenant, condition=condition)
    watched = await _open(db, tenant, created.id, f"check:{kept.id}")
    unwatched = await _open(db, tenant, created.id, f"check:{dropped.id}")

    await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(condition=condition | {"check_ids": [str(kept.id)]}),
    )
    assert await _still_open(db, watched, unwatched) == [True, False]
    assert await _positions(db, created.id) == [f"check:{kept.id}"]


async def test_narrowing_a_service_rule_closes_the_service_it_dropped(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    condition = {
        "kind": "service_error_rate",
        "services": ["cart", "checkout"],
        "threshold_percent": 5,
    }
    created = await _create(db, tenant, condition=condition)
    cart = await _open(db, tenant, created.id, "service:cart")
    checkout = await _open(db, tenant, created.id, "service:checkout")

    await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(condition=condition | {"services": ["checkout"]}),
    )
    assert await _still_open(db, cart, checkout) == [False, True]


async def test_an_incident_outlives_its_check_and_says_the_check_is_gone(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """Every alert links to its incident, so the incident must still read when the
    check it was about is deleted — and say so, rather than link to "No such check"."""
    check = await _check(db, tenant)
    rules = await service.list_rules(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    incident = await _open(db, tenant, rules.rules[0].id, f"check:{check.id}")
    incident.detail = {"check_id": str(check.id), "state": "down"}
    await db.flush()

    before = await service.get_incident(db, org_id=tenant.organization.id, incident_id=incident.id)
    assert before.check_deleted is False

    await checks.remove(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, check_id=check.id
    )

    after = await service.get_incident(db, org_id=tenant.organization.id, incident_id=incident.id)
    assert after.check_deleted is True
    assert after.subject_label == incident.subject_label


async def test_deleting_a_check_closes_its_open_incidents(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    check = await _check(db, tenant)
    rules = await service.list_rules(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    incident = await _open(db, tenant, rules.rules[0].id, f"check:{check.id}")

    await checks.remove(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, check_id=check.id
    )
    assert await _still_open(db, incident) == [False]


async def _told(db: AsyncSession, tenant: ProvisionedTenant, incident: Incident) -> uuid.UUID:
    """The incident was sent to the owners and to a webhook; returns the webhook."""
    hook = NotificationChannel(
        org_id=tenant.organization.id,
        kind="webhook",
        name="Ops bot",
        config={"url": "https://hooks.example.test/nexovue"},
    )
    db.add(hook)
    await db.flush()
    for channel_id in (await _owners(db, tenant), hook.id):
        db.add(
            Notification(
                org_id=tenant.organization.id,
                project_id=tenant.project.id,
                incident_id=incident.id,
                channel_id=channel_id,
                kind="opened",
                severity="critical",
                state="delivered",
            )
        )
    await db.flush()
    return hook.id


async def _closes(db: AsyncSession, incident: Incident) -> list[uuid.UUID]:
    found = await db.scalars(
        select(Notification.channel_id).where(
            Notification.incident_id == incident.id, Notification.kind == "closed"
        )
    )
    return list(found)


async def test_deleting_a_rule_tells_the_machines_it_alerted_that_the_incident_closed(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """Decision 2026-09-19: PagerDuty and webhooks would otherwise keep the alert open
    for ever. People are not told — nothing recovered, and they chose the change."""
    created = await _create(db, tenant)
    incident = await _open(db, tenant, created.id, f"check:{uuid.uuid4()}")
    hook = await _told(db, tenant, incident)

    await service.delete_rule(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, rule_id=created.id
    )
    assert await _closes(db, incident) == [hook]


async def test_disabling_a_rule_tells_the_machines_it_alerted_too(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    created = await _create(db, tenant)
    incident = await _open(db, tenant, created.id, f"check:{uuid.uuid4()}")
    hook = await _told(db, tenant, incident)

    await service.update_rule(
        db,
        org_id=tenant.organization.id,
        actor_user_id=tenant.user.id,
        rule_id=created.id,
        request=UpdateRuleRequest(enabled=False),
    )
    assert await _closes(db, incident) == [hook]


async def test_deleting_a_check_tells_the_machines_it_alerted_too(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    check = await _check(db, tenant)
    rules = await service.list_rules(
        db, org_id=tenant.organization.id, project_id=tenant.project.id
    )
    incident = await _open(db, tenant, rules.rules[0].id, f"check:{check.id}")
    hook = await _told(db, tenant, incident)

    await checks.remove(
        db, org_id=tenant.organization.id, actor_user_id=tenant.user.id, check_id=check.id
    )
    assert await _closes(db, incident) == [hook]
