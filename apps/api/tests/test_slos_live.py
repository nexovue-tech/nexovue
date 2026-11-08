"""Reliability targets through `/slos`, against real Postgres (F19, plan T1.7).

AC32 (who writes), AC33 (a token reads, never writes, sees no person, and is held to its
project), AC34 (each write's audit entry and history row), AC40 (the list reads no
ClickHouse: the repository is a stub that fails if asked), AC71's first half (a deleted
target leaves the list, shows its line, and is restored), AC74 (the 201st target),
AC105 (one definition once), AC116 (a deleted name is free; restoring into a taken one
asks for a new name), and FR5 (sampling Yes starts with alerts off).

Step 2: every kind is created with its own fields (AC3: 500 ms is stored and shown as
487.2 ms), AC4 (no route makes an endpoint latency target), a check from another
project is refused, and FR34's left-out endpoints are stored and change what is counted.

Rows are written inside one outer transaction per test and rolled back afterwards; the
team and client helpers are F22's (`test_findings_cases_live.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from nexovue_platform import slo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _call, _team, db  # noqa: F401  shared fixtures

from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    Check,
    ServiceLastSeen,
    SloTarget,
    SloTargetChange,
)

PERSON_FIELDS = ("created_by", "deleted_by", "removed_by", "actor_user_id")


async def _reported(db: AsyncSession, team: Team, project_id: uuid.UUID) -> None:  # noqa: F811
    """The project has heard from `checkout`, as the usage meter records it."""
    db.add(
        ServiceLastSeen(
            org_id=team.org_id,
            project_id=project_id,
            service_name="checkout",
            last_seen_at=datetime.now(UTC),
        )
    )
    await db.flush()


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    team = await _team(db, "Acme")
    await _reported(db, team, team.project_id)
    return team


@pytest.fixture
async def globex(db: AsyncSession) -> Team:  # noqa: F811
    team = await _team(db, "Globex")
    await _reported(db, team, team.project_id)
    return team


def _body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "checkout availability",
        "service_name": "checkout",
        "objective_percent": "99.9",
        "window_days": 28,
        "upstream_sampling": "no",
    }
    body.update(changes)
    return body


async def _create(db: AsyncSession, team: Team, **changes: Any) -> dict[str, Any]:  # noqa: F811
    created = await _call(
        db, team, team.member, "POST", f"/slos?project_id={team.project_id}", _body(**changes)
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


def _holds_no_person(payload: Any) -> bool:
    """No key at any depth names a person's id (AC33)."""
    if isinstance(payload, dict):
        return not set(payload) & set(PERSON_FIELDS) and all(
            _holds_no_person(value) for value in payload.values()
        )
    if isinstance(payload, list):
        return all(_holds_no_person(item) for item in payload)
    return True


# ----------------------------------------------------------------- AC32, AC34


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_ac32_every_working_role_creates_and_a_viewer_is_refused(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    role: str,
) -> None:
    person = getattr(acme, role)
    created = await _call(
        db, acme, person, "POST", f"/slos?project_id={acme.project_id}", _body(name=role)
    )
    refused = await _call(
        db, acme, acme.viewer, "POST", f"/slos?project_id={acme.project_id}", _body(name="v")
    )
    assert created.status_code == 201, created.text
    assert refused.status_code == 403


async def test_a_new_target_reads_counting_with_its_composed_label(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme)
    assert target["state"] == slo.State.COUNTING
    assert target["label"] == "checkout availability, 99.9% over 28 days"
    assert target["allowance_words"] == "Failures still allowed"


async def test_ac34_each_write_records_its_audit_entry_and_history_row(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme)
    path = f"/slos/{target['id']}"
    changed = await _call(db, acme, acme.member, "PATCH", path, {"objective_percent": "99.5"})
    deleted = await _call(db, acme, acme.member, "DELETE", path)
    restored = await _call(db, acme, acme.member, "POST", f"{path}/restore", {})
    assert (changed.status_code, deleted.status_code, restored.status_code) == (200, 204, 200)

    actions = (
        await db.scalars(
            select(AuditLogEntry.action)
            .where(AuditLogEntry.target == f"slo:{target['id']}")
            .order_by(AuditLogEntry.id)
        )
    ).all()
    assert actions == [
        audit.SLO_CREATED,
        audit.SLO_TARGET_CHANGED,
        audit.SLO_DELETED,
        audit.SLO_RESTORED,
    ]
    details = (
        await db.scalars(
            select(AuditLogEntry.detail).where(AuditLogEntry.target == f"slo:{target['id']}")
        )
    ).all()
    assert all(detail == {"project_id": str(acme.project_id)} for detail in details)
    history = (
        await db.scalars(
            select(SloTargetChange.what)
            .where(SloTargetChange.slo_id == uuid.UUID(target["id"]))
            .order_by(SloTargetChange.at, SloTargetChange.id)
        )
    ).all()
    assert history == ["created", "changed", "deleted", "restored"]


# ------------------------------------------------------------------------ AC33


async def test_ac33_a_token_reads_and_sees_no_person(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme)
    second = await _create(db, acme, name="old", objective_percent="99")
    await _call(db, acme, acme.member, "DELETE", f"/slos/{second['id']}")

    listed = await _call(db, acme, None, "GET", f"/slos?project_id={acme.project_id}")
    one = await _call(db, acme, None, "GET", f"/slos/{target['id']}")
    # A token on a write is refused by `CurrentIdentity` before the route runs, which
    # `test_authorization_coverage.py` holds for every write here; this harness has no
    # session store for that dependency to consult.

    assert (listed.status_code, one.status_code) == (200, 200)
    assert _holds_no_person(listed.json()) and _holds_no_person(one.json())
    assert listed.json()["deleted"][0]["deleted_by_name"] is None
    person = await _call(db, acme, acme.viewer, "GET", f"/slos?project_id={acme.project_id}")
    assert person.json()["deleted"][0]["deleted_by_name"] == "Member"


async def test_ac33_a_token_of_one_project_is_refused_another_projects_target(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    other = await _call(
        db, acme, acme.owner, "POST", "/projects", {"name": "staging", "slug": "staging"}
    )
    assert other.status_code in (200, 201), other.text
    elsewhere = uuid.UUID(other.json()["id"])
    await _reported(db, acme, elsewhere)
    created = await _call(db, acme, acme.member, "POST", f"/slos?project_id={elsewhere}", _body())
    assert created.status_code == 201, created.text

    read = await _call(db, acme, None, "GET", f"/slos/{created.json()['id']}")
    listed = await _call(db, acme, None, "GET", f"/slos?project_id={elsewhere}")
    assert (read.status_code, listed.status_code) == (404, 404)


# ------------------------------------------------------------------------ AC35


async def test_ac35_another_organization_can_neither_read_nor_change_a_target(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    target = await _create(db, acme)
    path = f"/slos/{target['id']}"
    assert (await _call(db, globex, globex.owner, "GET", path)).status_code == 404
    assert (await _call(db, globex, globex.owner, "PATCH", path, {"name": "x"})).status_code == 404
    assert (await _call(db, globex, globex.owner, "DELETE", path)).status_code == 404
    listed = await _call(db, globex, globex.owner, "GET", f"/slos?project_id={acme.project_id}")
    assert listed.status_code == 404


# ------------------------------------------------------------------------ AC71


async def test_ac71_a_deleted_target_leaves_the_list_shows_its_line_and_is_restored(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme)
    await _call(db, acme, acme.member, "DELETE", f"/slos/{target['id']}")

    listed = (
        await _call(db, acme, acme.viewer, "GET", f"/slos?project_id={acme.project_id}")
    ).json()
    assert listed["targets"] == []
    (line,) = listed["deleted"]
    assert line["name"] == "checkout availability"
    assert line["purged_on"] > line["deleted_at"][:10]
    assert (await _call(db, acme, acme.viewer, "GET", f"/slos/{target['id']}")).status_code == 404

    restored = await _call(db, acme, acme.member, "POST", f"/slos/{target['id']}/restore", {})
    assert restored.status_code == 200
    again = (
        await _call(db, acme, acme.viewer, "GET", f"/slos?project_id={acme.project_id}")
    ).json()
    assert [row["id"] for row in again["targets"]] == [target["id"]]


# ------------------------------------------------------------- AC74, AC105, AC116


async def test_ac74_the_201st_target_is_refused_with_the_protective_wording(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    for number in range(slo.TARGETS_PER_PROJECT):
        db.add(
            SloTarget(
                org_id=acme.org_id,
                project_id=acme.project_id,
                name=f"target {number}",
                kind=slo.SERVICE_AVAILABILITY,
                service_name=f"service-{number}",
                objective_percent=Decimal("99.9"),
                window_days=28,
                upstream_sampling="no",
                definition_key=f"key-{number}",
            )
        )
    await db.flush()
    refused = await _call(
        db, acme, acme.member, "POST", f"/slos?project_id={acme.project_id}", _body()
    )
    assert refused.status_code == 409
    assert "protects the system, not your plan" in refused.json()["detail"]


async def test_ac105_a_second_target_with_one_definition_is_refused(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    await _create(db, acme)
    again = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(name="another name", objective_percent="99.90"),
    )
    assert again.status_code == 409
    assert again.json()["field"] == "definition"


async def test_ac116_a_deleted_name_is_free_and_restoring_into_it_asks_for_a_new_one(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    first = await _create(db, acme)
    await _call(db, acme, acme.member, "DELETE", f"/slos/{first['id']}")
    await _create(db, acme, objective_percent="99.5")

    taken = await _call(db, acme, acme.member, "POST", f"/slos/{first['id']}/restore", {})
    renamed = await _call(
        db, acme, acme.member, "POST", f"/slos/{first['id']}/restore", {"name": "checkout 99.9"}
    )
    assert (taken.status_code, taken.json()["field"]) == (409, "name")
    assert renamed.status_code == 200 and renamed.json()["name"] == "checkout 99.9"


# --------------------------------------------------------------------- FR2, FR5


async def test_fr2_a_too_fine_objective_is_refused_beside_its_field(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    refused = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(objective_percent="99.999"),
    )
    assert refused.status_code == 422
    assert refused.json()["field"] == "objective_percent"


async def test_fr5_sampling_yes_starts_with_alerts_off(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme, upstream_sampling="yes")
    assert target["enabled"] is False
    assert target["state"] == slo.State.DISABLED


async def test_a_service_the_project_never_reported_is_refused_beside_its_field(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """Design, *Tenancy path*: a target's source is checked against the project at write."""
    refused = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(service_name="never-sent-anything"),
    )
    assert refused.status_code == 422
    assert refused.json()["field"] == "service_name"


# --------------------------------------------------------- Step 2: four kinds


async def _check(db: AsyncSession, team: Team, *, interval: int = 300) -> uuid.UUID:  # noqa: F811
    check = Check(
        org_id=team.org_id,
        project_id=team.project_id,
        name="Checkout API",
        type="tcp",
        interval_seconds=interval,
        config={"type": "tcp", "host": "example.test", "port": 443},
    )
    db.add(check)
    await db.flush()
    return check.id


async def test_ac3_a_latency_target_asked_for_500_ms_stores_and_shows_487_2(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme, kind="service_latency", name="checkout latency", asked_ms=500)
    assert target["asked_ms"] == 500
    assert round(target["boundary_ms"], 1) == 487.2
    assert target["label"] == "checkout latency under 487.2 ms, 99.9% over 28 days"
    assert target["allowance_words"] == "Slow requests still allowed"
    stored = await db.scalar(select(SloTarget).where(SloTarget.id == uuid.UUID(target["id"])))
    assert stored is not None and stored.boundary_index == slo.boundary_at_or_under(500.0)


@pytest.mark.parametrize("asked_ms", [0.5, 60_000])
async def test_ac101_a_threshold_outside_the_boundaries_is_refused_beside_the_field(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    asked_ms: float,
) -> None:
    refused = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(kind="service_latency", asked_ms=asked_ms),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "asked_ms"


async def test_ac4_no_route_makes_an_endpoint_latency_target(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    path = f"/slos?project_id={acme.project_id}"
    no_kind = await _call(db, acme, acme.member, "POST", path, _body(kind="endpoint_latency"))
    latency_with_endpoint = await _call(
        db,
        acme,
        acme.member,
        "POST",
        path,
        _body(kind="service_latency", asked_ms=500, endpoint_name="POST /orders"),
    )
    target = await _create(db, acme, kind="service_latency", name="latency", asked_ms=500)
    pointed = await _call(
        db, acme, acme.member, "PATCH", f"/slos/{target['id']}", {"endpoint_name": "GET /a"}
    )
    assert no_kind.status_code == 422
    assert latency_with_endpoint.status_code == 422
    assert latency_with_endpoint.json()["field"] == "endpoint_name"
    assert pointed.status_code == 422
    assert pointed.json()["field"] == "endpoint_name"


async def test_an_endpoint_target_names_its_service_and_endpoint(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(
        db, acme, kind="endpoint_availability", name="orders", endpoint_name="POST /orders"
    )
    assert target["label"] == "checkout POST /orders availability, 99.9% over 28 days"
    missing = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(kind="endpoint_availability", name="no endpoint"),
    )
    assert missing.status_code == 422
    assert missing.json()["field"] == "endpoint_name"


async def test_a_check_target_names_its_check_and_is_asked_nothing_about_sampling(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    check_id = await _check(db, acme)
    elsewhere = await _check(db, globex)
    body = {
        "kind": "check_availability",
        "name": "checkout check",
        "check_id": str(check_id),
        "objective_percent": "99.5",
        "window_days": 28,
    }
    path = f"/slos?project_id={acme.project_id}"
    created = await _call(db, acme, acme.member, "POST", path, body)
    foreign = await _call(
        db, acme, acme.member, "POST", path, body | {"check_id": str(elsewhere), "name": "x"}
    )
    asked = await _call(
        db, acme, acme.member, "POST", path, body | {"upstream_sampling": "no", "name": "y"}
    )
    assert created.status_code == 201, created.text
    target = created.json()
    assert target["label"] == "Checkout API availability, 99.5% over 28 days"
    assert target["allowance_words"] == "Failed runs still allowed"
    assert target["check_interval_seconds"] == 300
    assert target["upstream_sampling"] is None
    assert (foreign.status_code, foreign.json()["field"]) == (422, "check_id")
    assert (asked.status_code, asked.json()["field"]) == (422, "upstream_sampling")


async def test_ac80_left_out_endpoints_are_stored_and_changing_them_recounts(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    target = await _create(db, acme, left_out_endpoints=["GET /health"])
    assert target["left_out_endpoints"] == ["GET /health"]
    stored = await db.scalar(select(SloTarget).where(SloTarget.id == uuid.UUID(target["id"])))
    assert stored is not None
    version = stored.definition_version

    changed = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/slos/{target['id']}",
        {"left_out_endpoints": ["GET /health", "GET /readyz"]},
    )
    twice = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/slos/{target['id']}",
        {"left_out_endpoints": ["GET /health", "GET /health"]},
    )
    await db.refresh(stored)
    assert changed.status_code == 200, changed.text
    assert stored.definition_version == version + 1
    assert (twice.status_code, twice.json()["field"]) == (422, "left_out_endpoints")


async def test_fr34_only_a_service_availability_target_leaves_endpoints_out(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    refused = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/slos?project_id={acme.project_id}",
        _body(kind="service_latency", asked_ms=500, left_out_endpoints=["GET /health"]),
    )
    assert (refused.status_code, refused.json()["field"]) == (422, "left_out_endpoints")
