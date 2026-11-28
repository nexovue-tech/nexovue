"""A finding and an incident on one service are linked, never merged (plan T4.5;
spec BR2, AC20; design *Findings and their state*).

This side is the incident's: a threshold or *went silent* incident that opens on a
service with an open finding is linked to it in the transaction that opens it. The
other side — a finding that opens second — is the project job's, judged in
`test_detection_project_job_live.py`.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from alert_fixtures import (
    AlertTenant,
    add_finding,
    add_rule,
    future,
    postgres_reachable,
    rows,
)
from live import require_stack
from nexovue_platform.alerts.model import check_subject, service_subject
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import incidents
from nexovue_scheduler.alerts.rules import Rule

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_detection_links_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

ERROR_RATE = {"kind": "service_error_rate", "threshold_percent": 5}


async def _rule(engine: AsyncEngine, tenant: AlertTenant, condition: dict[str, Any]) -> Rule:
    async with engine.begin() as connection:
        rule_id = await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name=str(condition["kind"]),
            condition=condition,
            channel_ids=[],
        )
    return Rule(
        id=rule_id,
        org_id=tenant.org_id,
        project_id=tenant.project_id,
        name=str(condition["kind"]),
        condition_kind=str(condition["kind"]),
        condition=condition,
        muted_until=None,
        channel_ids=(),
    )


async def _fire(engine: AsyncEngine, rule: Rule, key: str, label: str) -> None:
    async with engine.begin() as connection:
        await incidents.fire(
            connection,
            rule,
            incidents.Subject(key=key, label=label),
            severity="critical",
            at=future(),
            detail={},
        )


async def _links(engine: AsyncEngine, tenant: AlertTenant) -> list[tuple[uuid.UUID, str]]:
    found = await rows(
        engine,
        """
        SELECT finding_incidents.finding_id, incidents.subject_key
        FROM finding_incidents
        JOIN incidents ON incidents.id = finding_incidents.incident_id
        WHERE finding_incidents.org_id = :org_id
        """,
        org_id=tenant.org_id,
    )
    return [(row.finding_id, row.subject_key) for row in found]


async def test_a_threshold_incident_on_a_service_with_an_open_finding_is_linked_to_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC20: one finding, linked to one threshold incident — not a second finding."""
    async with engine.begin() as connection:
        finding = await add_finding(connection, tenant, started_at=future() - timedelta(minutes=5))
    rule = await _rule(engine, tenant, ERROR_RATE)

    await _fire(engine, rule, service_subject("checkout"), "checkout")

    assert await _links(engine, tenant) == [(finding, service_subject("checkout"))]
    findings = await rows(
        engine, "SELECT id FROM findings WHERE org_id = :org_id", org_id=tenant.org_id
    )
    assert [row.id for row in findings] == [finding]


async def test_a_silence_incident_is_linked_too(engine: AsyncEngine, tenant: AlertTenant) -> None:
    async with engine.begin() as connection:
        finding = await add_finding(connection, tenant, started_at=future() - timedelta(minutes=5))
    rule = await _rule(engine, tenant, {"kind": "service_silent"})

    await _fire(engine, rule, service_subject("checkout"), "checkout")

    assert await _links(engine, tenant) == [(finding, service_subject("checkout"))]


async def test_only_an_open_finding_on_the_same_service_is_linked(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    at = future()
    async with engine.begin() as connection:
        await add_finding(
            connection,
            tenant,
            started_at=at - timedelta(hours=2),
            closed_at=at - timedelta(hours=1),
        )
        await add_finding(connection, tenant, service="cart", started_at=at - timedelta(minutes=5))
    rule = await _rule(engine, tenant, ERROR_RATE)

    await _fire(engine, rule, service_subject("checkout"), "checkout")

    assert await _links(engine, tenant) == []


async def test_a_check_incident_names_no_service_and_is_never_linked(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    async with engine.begin() as connection:
        await add_finding(connection, tenant, started_at=future() - timedelta(minutes=5))
    rule = await _rule(engine, tenant, {"kind": "check_state", "states": {"down": "critical"}})

    await _fire(engine, rule, check_subject(uuid.uuid4()), "checkout")

    assert await _links(engine, tenant) == []


# ------------------------------------------------------------------ F19 (T5.2, FR20)

BURN = {"kind": "slo_burn", "slo_ids": None, "minimum_severity": "warning"}


def _burn_subject(service: str | None, *, check_id: uuid.UUID | None = None) -> incidents.Subject:
    """A burn's subject as the job writes it: keyed by its target, about its service."""
    return incidents.Subject(
        key=f"slo:{uuid.uuid4()}",
        label=f"{service} availability, 99.9% over 28 days",
        about_key=service_subject(service) if service else check_subject(check_id or uuid.uuid4()),
        about_signal="availability",
        about_check_id=check_id,
    )


async def _fire_subject(engine: AsyncEngine, rule: Rule, subject: incidents.Subject) -> None:
    async with engine.begin() as connection:
        await incidents.fire(connection, rule, subject, severity="critical", at=future(), detail={})


async def test_a_burn_on_a_service_with_an_open_finding_is_linked_to_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR20, the finding first: the burn's key is its target, its `about_key` the service."""
    async with engine.begin() as connection:
        finding = await add_finding(connection, tenant, started_at=future() - timedelta(minutes=5))
    rule = await _rule(engine, tenant, BURN)
    subject = _burn_subject("checkout")

    await _fire_subject(engine, rule, subject)

    assert await _links(engine, tenant) == [(finding, subject.key)]
    [written] = await rows(
        engine,
        "SELECT about_key, about_signal, about_endpoint, about_check_id FROM incidents "
        "WHERE org_id = :org_id",
        org_id=tenant.org_id,
    )
    assert tuple(written) == ("service:checkout", "availability", None, None)


async def test_a_finding_opening_after_a_burn_on_its_service_is_linked_to_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR20, the burn first: the project job's link matches a burn by its `about_key`."""
    from nexovue_scheduler.detection.project_job import _link_incidents

    rule = await _rule(engine, tenant, BURN)
    subject = _burn_subject("checkout")
    await _fire_subject(engine, rule, subject)
    other = _burn_subject("cart")
    await _fire_subject(engine, rule, other)
    async with engine.begin() as connection:
        finding = await add_finding(connection, tenant, started_at=future())
        await _link_incidents(
            connection,
            {"org_id": tenant.org_id, "project_id": tenant.project_id, "service_name": "checkout"},
            finding,
        )

    assert await _links(engine, tenant) == [(finding, subject.key)]


async def test_a_check_burn_naming_no_service_is_never_linked(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    async with engine.begin() as connection:
        await add_finding(connection, tenant, started_at=future() - timedelta(minutes=5))
    rule = await _rule(engine, tenant, BURN)

    await _fire_subject(engine, rule, _burn_subject(None, check_id=uuid.uuid4()))

    assert await _links(engine, tenant) == []
