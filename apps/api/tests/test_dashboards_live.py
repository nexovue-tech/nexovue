"""Saved dashboards through `/dashboards`, against real Postgres (F20, T2.6, T2.7, T2.14, T2.15).

AC6 (another organization's dashboard answers as an unknown id does), AC7 (every
working role writes, a viewer is refused), AC8's write half (a support view reads and is
refused every write), AC10 (two saves from one version: the second is a conflict naming
who saved), AC11 (titles unique ignoring case; a deleted one's title is free), AC13 (each
write's one audit entry: title, panel count, version and hash, a rename's old title, link
hosts, and never the definition), AC38 (opening a template writes nothing), AC56's trim
(past the newest 20 and older than 30 days only), AC61 (the context answers while
ClickHouse is unreachable, and the batch says 503), AC75 (the two 404s, each its own
type), AC96 (lists read columns, never a definition), AC125 (a save changing nothing
makes no version; 20 quick saves keep the earlier one), FR9 (the caps), and T2.14's two
findings reads past 200.

ClickHouse is absent from every route here but the batch: the tenant repository is
F22's stub, which fails if a read runs before the request's commit.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final

import clickhouse_connect
import httpx
import pytest
from environment import TEST_ENVIRONMENT
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _call, _client, _team, db  # noqa: F401

from nexovue_api.audit import service as audit
from nexovue_api.dashboards import service as dashboards
from nexovue_api.dashboards.templates import START_FROM_OVERVIEW
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    Dashboard,
    DashboardVersion,
    Finding,
    ServiceLastSeen,
)
from nexovue_api.deps import get_app_settings, get_resources
from nexovue_api.lifespan import ClickHouseConnection
from nexovue_api.settings import API_PREFIX, Settings

SECRET_NOTE: Final = "paste: hunter2-not-a-real-secret"
UNREACHABLE_CLICKHOUSE_PORT: Final = 1


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    return await _team(db, "Acme")


@pytest.fixture
async def globex(db: AsyncSession) -> Team:  # noqa: F811
    return await _team(db, "Globex")


def _document(title: str = "Checkout on-call", **definition: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "refresh_seconds": 60,
        "default_range": None,
        "service_picker": False,
        "links": [{"label": "Runbook", "url": "https://wiki.example/checkout"}],
        "panels": [
            {
                "id": "p_rate",
                "kind": "service_trend",
                "title": None,
                "width": 4,
                "height": "medium",
                "settings": {"service": "checkout", "signal": "rate"},
            },
            {
                "id": "p_project",
                "kind": "project_trend",
                "title": "Errors",
                "width": 4,
                "height": "medium",
                "settings": {"signal": "errors"},
            },
            {
                "id": "p_note",
                "kind": "note",
                "title": None,
                "width": 4,
                "height": "small",
                "settings": {"text": SECRET_NOTE},
            },
        ],
    }
    body.update(definition)
    return {"title": title, "description": "What the checkout team watches.", "definition": body}


async def _finding(
    db: AsyncSession,  # noqa: F811
    team: Team,
    *,
    started_at: datetime,
    closed_at: datetime | None = None,
    scope: str = "service",
    service: str | None = "checkout",
) -> Finding:
    row = Finding(
        org_id=team.org_id,
        project_id=team.project_id,
        scope=scope,
        service_name=service,
        severity="warning",
        state="closed" if closed_at else "open",
        closed_reason="recovered" if closed_at else None,
        title=f"{service or 'this project'} is slower than normal",
        signals=[],
        started_at=started_at,
        closed_at=closed_at,
    )
    db.add(row)
    await db.flush()
    return row


async def _create(db: AsyncSession, team: Team, **changes: Any) -> dict[str, Any]:  # noqa: F811
    created = await _call(db, team, team.member, "POST", "/dashboards", _document(**changes))
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _save(
    db: AsyncSession,  # noqa: F811
    team: Team,
    saved: dict[str, Any],
    **changes: Any,
) -> httpx.Response:
    body = {
        "version": saved["version"],
        "title": saved["title"],
        "description": saved["description"],
        "definition": saved["definition"],
    } | changes
    return await _call(db, team, team.member, "PUT", f"/dashboards/{saved['id']}", body)


async def _entries(db: AsyncSession, team: Team, action: str) -> list[AuditLogEntry]:  # noqa: F811
    statement = (
        select(AuditLogEntry)
        .where(AuditLogEntry.org_id == team.org_id, AuditLogEntry.action == action)
        .order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(await db.scalars(statement))


async def _versions(db: AsyncSession, dashboard_id: str) -> list[int]:  # noqa: F811
    statement = (
        select(DashboardVersion.version)
        .where(DashboardVersion.dashboard_id == uuid.UUID(dashboard_id))
        .order_by(DashboardVersion.version)
    )
    return list(await db.scalars(statement))


# ------------------------------------------------------------------ AC7, AC8


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_ac7_every_working_role_writes(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    role: str,
) -> None:
    person = getattr(acme, role)
    created = await _call(db, acme, person, "POST", "/dashboards", _document(title=role))

    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1


async def test_ac7_a_viewer_reads_and_is_refused_every_write(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    path = f"/dashboards/{saved['id']}"
    body = {**_document(), "version": saved["version"]}

    assert (await _call(db, acme, acme.viewer, "GET", path)).status_code == 200
    assert (await _call(db, acme, acme.viewer, "GET", "/dashboards")).status_code == 200
    for method, target, payload in (
        ("POST", "/dashboards", _document(title="v")),
        ("PUT", path, body),
        ("DELETE", path, None),
    ):
        refused = await _call(db, acme, acme.viewer, method, target, payload)
        assert refused.status_code == 403, (method, refused.text)


async def test_ac8_a_support_view_reads_every_dashboard_route(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)

    for path in (
        "/dashboards",
        f"/dashboards/{saved['id']}",
        "/dashboards/templates",
        f"/dashboards/{saved['id']}/context?project_id={acme.project_id}",
    ):
        read = await _call(db, acme, acme.owner, "GET", path, support_view=True)
        assert read.status_code == 200, (path, read.text)


def test_ac8_every_dashboard_write_passes_the_support_views_method_gate() -> None:
    """A support view's writes are refused below every route, in `get_current_identity`
    (`_hold_support_view`), which this harness overrides; so this holds each write's gate
    to that dependency rather than calling it."""
    from fastapi.dependencies.utils import get_typed_signature

    from nexovue_api.auth.dependencies import get_current_identity
    from nexovue_api.dashboards import router as dashboards_router

    for handler in (
        dashboards_router.create_dashboard,
        dashboards_router.replace_dashboard,
        dashboards_router.delete_dashboard,
    ):
        identity = get_typed_signature(handler).parameters["identity"]
        [gate] = [item for item in identity.annotation.__metadata__ if hasattr(item, "dependency")]
        [person] = [
            parameter.annotation.__metadata__[0].dependency
            for parameter in get_typed_signature(gate.dependency).parameters.values()
        ]
        assert person is get_current_identity, handler.__name__


# ----------------------------------------------------------------- AC6, AC75


async def test_ac6_another_organizations_dashboard_answers_as_an_unknown_id_does(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    theirs = await _create(db, globex)
    unknown = str(uuid.uuid4())

    for method, body in (("GET", None), ("PUT", {**_document(), "version": 1}), ("DELETE", None)):
        foreign = await _call(db, acme, acme.member, method, f"/dashboards/{theirs['id']}", body)
        missing = await _call(db, acme, acme.member, method, f"/dashboards/{unknown}", body)
        assert foreign.status_code == missing.status_code == 404
        assert _without_instance(foreign.json()) == _without_instance(missing.json())
    still = await _call(db, globex, globex.member, "GET", f"/dashboards/{theirs['id']}")
    assert still.status_code == 200


def _without_instance(problem: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in problem.items() if key not in ("instance", "correlation_id")
    }


async def test_ac75_the_dashboard_and_the_project_404s_each_have_their_own_type(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    saved = await _create(db, acme)
    no_dashboard = await _call(
        db,
        acme,
        acme.member,
        "GET",
        f"/dashboards/{uuid.uuid4()}/context?project_id={acme.project_id}",
    )
    no_project = await _call(
        db, acme, acme.member, "GET", f"/dashboards/{saved['id']}/context?project_id={uuid.uuid4()}"
    )
    foreign_project = await _call(
        db,
        acme,
        acme.member,
        "GET",
        f"/dashboards/{saved['id']}/context?project_id={globex.project_id}",
    )

    assert no_dashboard.status_code == no_project.status_code == foreign_project.status_code == 404
    assert no_dashboard.json()["type"].endswith("/dashboard-not-found")
    assert no_project.json()["type"].endswith("/project-not-found")
    assert _without_instance(no_project.json()) == _without_instance(foreign_project.json())


# ---------------------------------------------------------------- AC10, AC11


async def test_ac10_the_second_save_from_one_version_is_a_conflict_naming_who_saved(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)

    first = await _save(db, acme, saved, title="Checkout, renamed")
    second = await _save(db, acme, saved, title="Checkout, mine")

    assert first.status_code == 200 and first.json()["version"] == 2
    assert second.status_code == 409
    assert second.json()["saved_by"] == acme.member.name
    assert second.json()["version"] == 2


async def test_ac11_a_title_differing_only_in_case_is_refused_naming_the_holder(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    holder = await _create(db, acme, title="Checkout")

    refused = await _call(db, acme, acme.member, "POST", "/dashboards", _document(title="checkout"))

    assert refused.status_code == 409
    assert refused.json()["existing_dashboard_id"] == holder["id"]


async def test_ac11_a_deleted_dashboards_title_may_be_used_again(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    gone = await _create(db, acme, title="Checkout")
    assert (
        await _call(db, acme, acme.member, "DELETE", f"/dashboards/{gone['id']}")
    ).status_code == 204

    again = await _call(db, acme, acme.member, "POST", "/dashboards", _document(title="CHECKOUT"))

    assert again.status_code == 201
    after = await _call(db, acme, acme.member, "GET", f"/dashboards/{gone['id']}")
    assert after.status_code == 404
    assert after.json()["type"].endswith("/dashboard-not-found")


async def test_two_organizations_may_use_one_title(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    await _create(db, acme, title="Checkout")

    assert (await _create(db, globex, title="Checkout"))["title"] == "Checkout"


# ----------------------------------------------------------------- AC4 over the wire


async def test_ac4_ac5_ac77_the_route_refuses_what_the_validator_refuses(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    query_kind = _document()
    query_kind["definition"]["panels"][0]["kind"] = "query"
    sql = _document()
    sql["definition"]["panels"][0]["settings"]["sql"] = "SELECT 1"
    script = _document(links=[{"label": "x", "url": "javascript:alert(1)"}])

    for body in (query_kind, sql, script):
        refused = await _call(db, acme, acme.member, "POST", "/dashboards", body)
        assert refused.status_code == 422, refused.text
    assert await db.scalar(select(func.count()).where(Dashboard.org_id == acme.org_id)) == 0


# ------------------------------------------------------------------- AC13


async def test_ac13_each_write_records_one_entry_with_facts_and_never_the_definition(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    renamed = await _save(
        db,
        acme,
        saved,
        title="Checkout, renamed",
        definition={
            **saved["definition"],
            "links": [{"label": "Board", "url": "https://boards.example/checkout"}],
        },
    )
    assert renamed.status_code == 200
    await _call(db, acme, acme.member, "DELETE", f"/dashboards/{saved['id']}")

    [created] = await _entries(db, acme, audit.DASHBOARD_CREATED)
    [updated] = await _entries(db, acme, audit.DASHBOARD_UPDATED)
    [deleted] = await _entries(db, acme, audit.DASHBOARD_DELETED)
    stored = await db.scalar(select(Dashboard).where(Dashboard.id == uuid.UUID(saved["id"])))
    assert stored is not None

    assert created.target == f"dashboard:{saved['id']}"
    assert created.actor_user_id == acme.member.id
    assert created.detail == {
        "title": "Checkout on-call",
        "panel_count": 3,
        "version": 1,
        "definition_sha256": _sha(saved["definition"]),
        "link_hosts_added": ["wiki.example"],
    }
    assert updated.detail is not None and deleted.detail is not None
    assert updated.detail["previous_title"] == "Checkout on-call"
    assert updated.detail["title"] == "Checkout, renamed"
    assert updated.detail["version"] == 2
    assert updated.detail["link_hosts_added"] == ["boards.example"]
    assert updated.detail["link_hosts_removed"] == ["wiki.example"]
    assert updated.detail["definition_sha256"] == _sha(stored.definition)
    assert "previous_title" not in deleted.detail
    for entry in (created, updated, deleted):
        assert SECRET_NOTE not in json.dumps(entry.detail)
        assert "checkout/" not in json.dumps(entry.detail)


def _sha(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ------------------------------------------------------------- AC125, AC56


async def test_ac125_a_save_changing_nothing_makes_no_version_and_no_entry(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)

    same = await _save(db, acme, saved)

    assert same.status_code == 200 and same.json()["version"] == 1
    assert await _versions(db, saved["id"]) == []
    assert await _entries(db, acme, audit.DASHBOARD_UPDATED) == []


async def test_ac125_twenty_quick_saves_leave_the_earlier_good_version_restorable(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    good = await _create(db, acme)
    current = good
    for index in range(20):
        answer = await _save(db, acme, current, title=f"wrecked {index}")
        assert answer.status_code == 200, answer.text
        current = answer.json()

    versions = await _versions(db, good["id"])
    first = await db.scalar(
        select(DashboardVersion).where(
            DashboardVersion.dashboard_id == uuid.UUID(good["id"]), DashboardVersion.version == 1
        )
    )

    assert versions == list(range(1, 21))
    assert first is not None and first.title == "Checkout on-call"
    assert first.definition == good["definition"]


async def test_ac56_a_save_trims_versions_past_twenty_and_older_than_30_days_only(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    dashboard_id = uuid.UUID(saved["id"])
    now = datetime.now(UTC)
    # 22 old versions, then 5 recent ones: the row stands at version 28.
    for version in range(1, 28):
        db.add(
            DashboardVersion(
                dashboard_id=dashboard_id,
                org_id=acme.org_id,
                version=version,
                title=f"v{version}",
                definition=saved["definition"],
                saved_at=now - timedelta(days=40 if version <= 22 else 1),
            )
        )
    await db.execute(update(Dashboard).where(Dashboard.id == dashboard_id).values(version=28))
    await db.flush()

    answer = await _save(db, acme, {**saved, "version": 28}, title="Checkout, again")

    assert answer.status_code == 200, answer.text
    kept = await _versions(db, saved["id"])
    # Version 28 joins; the newest 20 are 9..28. Of the older, all are past 30 days.
    assert kept == list(range(9, 29))


async def test_ac56_versions_newer_than_30_days_are_kept_past_twenty(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    dashboard_id = uuid.UUID(saved["id"])
    for version in range(1, 26):
        db.add(
            DashboardVersion(
                dashboard_id=dashboard_id,
                org_id=acme.org_id,
                version=version,
                title=f"v{version}",
                definition=saved["definition"],
                saved_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    await db.execute(update(Dashboard).where(Dashboard.id == dashboard_id).values(version=26))
    await db.flush()

    assert (await _save(db, acme, {**saved, "version": 26}, title="x")).status_code == 200

    assert await _versions(db, saved["id"]) == list(range(1, 27))


# ------------------------------------------------------------------- AC96


async def test_ac96_the_list_reads_columns_and_never_a_definition(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(
        db,
        acme,
        service_picker=True,
        panels=[
            *_document()["definition"]["panels"],
            {
                "id": "p_picked",
                "kind": "service_trend",
                "title": None,
                "width": 4,
                "height": "medium",
                "settings": {"service": {"picker": True}, "signal": "latency"},
            },
        ],
    )
    stored = await db.scalar(select(Dashboard).where(Dashboard.id == uuid.UUID(saved["id"])))
    assert stored is not None
    assert (stored.panel_count, stored.service_names, stored.uses_picker) == (4, ["checkout"], True)
    # A definition nothing could draw: the list still answers from its columns.
    await db.execute(
        text("UPDATE dashboards SET definition = '{\"broken\": true}' WHERE id = :id"),
        {"id": stored.id},
    )

    listed = await _call(db, acme, acme.viewer, "GET", "/dashboards")

    [row] = listed.json()["dashboards"]
    assert row["panel_count"] == 4 and row["uses_picker"] is True
    assert row["owner"] == acme.member.name
    assert "definition" not in row


# -------------------------------------------------------------------- FR9


async def test_fr9_the_thousand_and_first_live_dashboard_is_refused_saying_why(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    await _fill(db, acme, live=dashboards.LIVE_DASHBOARDS_CAP)

    refused = await _call(db, acme, acme.member, "POST", "/dashboards", _document(title="one more"))

    assert refused.status_code == 409
    assert "1,000 dashboards" in refused.json()["detail"]


async def test_fr9_deleted_dashboards_count_towards_the_second_cap(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    await _fill(db, acme, live=10, deleted=dashboards.ALL_DASHBOARDS_CAP - 10)

    refused = await _call(db, acme, acme.member, "POST", "/dashboards", _document(title="one more"))

    assert refused.status_code == 409
    assert "2,000 dashboards" in refused.json()["detail"]


async def _fill(db: AsyncSession, team: Team, *, live: int, deleted: int = 0) -> None:  # noqa: F811
    now = datetime.now(UTC)
    await db.execute(
        insert(Dashboard),
        [
            {
                "org_id": team.org_id,
                "title": f"filler {index}",
                "description": "",
                "definition": {"schema_version": 1, "panels": []},
                "deleted_at": now if index >= live else None,
            }
            for index in range(live + deleted)
        ],
    )


# ------------------------------------------------------------- AC38, AC80


async def test_ac38_opening_a_template_writes_no_row(db: AsyncSession, acme: Team) -> None:  # noqa: F811
    before = await db.scalar(select(func.count()).select_from(Dashboard))

    answer = await _call(db, acme, acme.viewer, "GET", "/dashboards/templates")

    assert answer.status_code == 200
    [template] = answer.json()["templates"]
    assert template["id"] == START_FROM_OVERVIEW
    assert [panel["kind"] for panel in template["document"]["definition"]["panels"]] == [
        "project_trend"
    ] * 3
    assert await db.scalar(select(func.count()).select_from(Dashboard)) == before


async def test_a_template_saves_as_it_is(db: AsyncSession, acme: Team) -> None:  # noqa: F811
    template = (await _call(db, acme, acme.member, "GET", "/dashboards/templates")).json()

    saved = await _call(
        db, acme, acme.member, "POST", "/dashboards", template["templates"][0]["document"]
    )

    assert saved.status_code == 201, saved.text


# ---------------------------------------------------------- T2.14, AC61


async def test_the_context_names_project_clock_detection_and_what_has_reported(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    seen = datetime.now(UTC) - timedelta(hours=3)
    db.add(
        ServiceLastSeen(
            org_id=acme.org_id,
            project_id=acme.project_id,
            service_name="checkout",
            last_seen_at=seen,
        )
    )
    open_one = await _finding(db, acme, started_at=datetime.now(UTC) - timedelta(minutes=20))
    await _finding(
        db,
        acme,
        scope="project",
        service=None,
        started_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    context = await _call(
        db,
        acme,
        acme.viewer,
        "GET",
        f"/dashboards/{saved['id']}/context?project_id={acme.project_id}&range=1h",
    )

    assert context.status_code == 200, context.text
    body = context.json()
    assert body["project"]["id"] == str(acme.project_id)
    assert body["project"]["timezone"]
    assert body["project"]["retention"] == {"raw_days": 7, "derived_days": 30}
    assert body["ever_reported"] is True
    assert body["last_seen_at"] == seen.isoformat().replace("+00:00", "Z")
    assert body["detection"]["state"] in {"not_running", "no_services", "learning", "judging"}
    assert [finding["scope"] for finding in body["findings"]] == ["project", "service"]
    assert body["findings"][1]["id"] == str(open_one.id)
    assert body["open_findings"] == {"total": 2, "by_service": {"checkout": 1}}


async def test_a_project_that_never_reported_says_so(db: AsyncSession, acme: Team) -> None:  # noqa: F811
    saved = await _create(db, acme)

    body = (
        await _call(
            db,
            acme,
            acme.viewer,
            "GET",
            f"/dashboards/{saved['id']}/context?project_id={acme.project_id}",
        )
    ).json()

    assert (body["ever_reported"], body["last_seen_at"]) == (False, None)


async def test_a_self_hosted_install_has_no_plan_retention(db: AsyncSession, acme: Team) -> None:  # noqa: F811
    saved = await _create(db, acme)
    client, _ = _client(db, acme, acme.viewer)
    settings = Settings(**(TEST_ENVIRONMENT | {"NEXOVUE_DEPLOYMENT": "self-hosted"}))  # type: ignore[arg-type]
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_app_settings] = lambda: settings

    async with client:
        answer = await client.get(
            f"{API_PREFIX}/dashboards/{saved['id']}/context?project_id={acme.project_id}"
        )

    assert answer.json()["project"]["retention"] is None


async def test_ac61_with_clickhouse_unreachable_the_batch_says_503_and_the_context_answers(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    saved = await _create(db, acme)
    client, _ = _client(db, acme, acme.viewer)
    app = client._transport.app  # type: ignore[attr-defined]

    async def unreachable() -> Any:
        return await clickhouse_connect.get_async_client(
            host="127.0.0.1", port=UNREACHABLE_CLICKHOUSE_PORT, connect_timeout=1
        )

    from nexovue_api.auth.dependencies import get_tenant_repository

    app.dependency_overrides.pop(get_tenant_repository)
    app.dependency_overrides[get_resources] = lambda: SimpleNamespace(
        clickhouse=ClickHouseConnection(unreachable)
    )
    query = f"dashboard_id={saved['id']}&project_id={acme.project_id}&range=1h"

    async with client:
        batch = await client.get(f"{API_PREFIX}/dashboard-panels/read?{query}")
        context = await client.get(
            f"{API_PREFIX}/dashboards/{saved['id']}/context?project_id={acme.project_id}&range=1h"
        )

    assert batch.status_code == 503
    assert batch.json()["type"].endswith("/dependency-unavailable")
    assert context.status_code == 200


async def test_both_findings_reads_hold_past_200(db: AsyncSession, acme: Team) -> None:  # noqa: F811
    """T2.14: the dashboard's read keeps the newest 200 and says it cut; the service
    timeline keeps its oldest 200 and says the same, unchanged (F6)."""
    saved = await _create(db, acme)
    start = datetime.now(UTC) - timedelta(minutes=50)
    for index in range(205):
        await _finding(
            db,
            acme,
            started_at=start + timedelta(seconds=index * 10),
            closed_at=start + timedelta(seconds=index * 10 + 5),
        )

    context = (
        await _call(
            db,
            acme,
            acme.viewer,
            "GET",
            f"/dashboards/{saved['id']}/context?project_id={acme.project_id}&range=1h",
        )
    ).json()
    timeline = (
        await _call(
            db,
            acme,
            acme.viewer,
            "GET",
            f"/services/checkout/detection?project_id={acme.project_id}",
        )
    ).json()

    assert len(context["findings"]) == 200 and context["findings_truncated"] is True
    newest = [finding["started_at"] for finding in context["findings"]]
    assert newest == sorted(newest, reverse=True)
    assert len(timeline["findings"]) == 200 and timeline["truncated"] is True
    oldest = [finding["started_at"] for finding in timeline["findings"]]
    assert oldest == sorted(oldest)
    assert oldest[0] < newest[-1]
