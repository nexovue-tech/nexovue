"""A case's tracker issue, from the API's side, against real Postgres (F22 plan T4.3;
AC90's and AC99's link halves, AC92's API half, AC37's disconnect half, AC88, AC114's
counts, and FR38's wanted state on every act that moves a case).

The API never calls a tracker (AC32): a click writes the link `creating`, with the
facts the preview showed frozen on it, and adds one sync job; a person's resolve or
reopen sets what the synced issue should become. `test_connections_sync_live.py` proves
the job then does it. Each test runs in one transaction, rolled back afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from nexovue_platform.connections import SYNC_CONNECTION_TASK, IssueFacts, issue_text
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_findings_cases_live import (
    ASYNC_DSN,
    NOW,
    CommitWitness,
    Team,
    _events,
    _finding,
    _fresh,
    _team,
)

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.connections import service as connections
from nexovue_api.data.postgres.models import AuditLogEntry, FindingLink, User
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.deps import get_app_settings, get_db_session, get_optional_email_sender
from nexovue_api.settings import API_PREFIX, Settings

CONSOLE: Final = "https://nexovue.acme.example"


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def acme(db: AsyncSession) -> Team:
    return await _team(db, "Acme")


@pytest.fixture
async def globex(db: AsyncSession) -> Team:
    return await _team(db, "Globex")


SIGNALS: Final = [
    {
        "signal": "slower",
        "current": 812.0,
        "normal": 203.0,
        "worst": 900.0,
        "comparison": "Tuesdays",
    }
]


async def _call(
    db: AsyncSession,
    team: Team,
    person: User | None,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    from nexovue_api.main import create_app

    settings = Settings(**{**TEST_ENVIRONMENT, "APP_BASE_URL": CONSOLE})  # type: ignore[arg-type]
    app = create_app(settings)
    organization = team.tenant.organization
    reader = (
        Reader(org_id=organization.id, project_scope=team.project_id, token_id=uuid.uuid4())
        if person is None
        else Reader(
            org_id=organization.id, identity=Identity(user=person, organization=organization)
        )
    )
    witness = CommitWitness()
    witness.committed = True

    async def session() -> Any:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_current_reader] = lambda: reader
    if person is not None:
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            user=person, organization=organization
        )
    app.dependency_overrides[get_tenant_repository] = lambda: witness
    app.dependency_overrides[get_optional_email_sender] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.request(method, f"{API_PREFIX}{path}", json=body)


async def _connected(
    db: AsyncSession, team: Team, *, state: str = "working", private: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    connection_id = (
        await db.execute(
            text(
                "INSERT INTO connections (org_id, kind, state, sealed_credential, "
                "github_app_id, github_installation_id) "
                "VALUES (:org, 'github', :state, :key, :app, 1) RETURNING id"
            ),
            {"org": team.org_id, "state": state, "key": b"sealed", "app": uuid.uuid4().int % 10**9},
        )
    ).scalar_one()
    place_id = (
        await db.execute(
            text(
                "INSERT INTO connection_repositories (org_id, connection_id, external_id, key, "
                "is_private) VALUES (:org, :connection, '42', 'acme/api', :private) RETURNING id"
            ),
            {"org": team.org_id, "connection": connection_id, "private": private},
        )
    ).scalar_one()
    return connection_id, place_id


async def _case_finding(db: AsyncSession, team: Team, **extra: Any) -> FindingRow:
    row = await _finding(db, team, severity="critical", **extra)
    row.signals = SIGNALS
    await db.flush()
    return row


async def _sync_jobs(db: AsyncSession, team: Team) -> int:
    return int(
        await db.scalar(
            text("SELECT count(*) FROM jobs WHERE org_id = :org AND task = :task"),
            {"org": team.org_id, "task": SYNC_CONNECTION_TASK},
        )
        or 0
    )


async def _link(db: AsyncSession, team: Team, link_id: uuid.UUID) -> FindingLink:
    row = await db.scalar(
        select(FindingLink)
        .where(FindingLink.org_id == team.org_id, FindingLink.id == link_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def _synced(
    db: AsyncSession,
    team: Team,
    finding: FindingRow,
    connection_id: uuid.UUID,
    place_id: uuid.UUID,
    *,
    seen: str = "open",
    held: bool = False,
    status: str = "in_sync",
) -> uuid.UUID:
    link = FindingLink(
        org_id=team.org_id,
        finding_id=finding.id,
        connection_id=connection_id,
        place_id=place_id,
        role="synced",
        sync_status=status,
        external_id="5000001",
        external_key="acme/api#1",
        seen_state=seen,
        tracker_closed_at=NOW if held else None,
    )
    db.add(link)
    await db.flush()
    return link.id


# --------------------------------------------------------------- the preview


async def test_the_preview_is_the_issue_s_exact_text_and_sends_nothing(
    db: AsyncSession, acme: Team
) -> None:
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)

    response = await _call(
        db, acme, acme.member, "GET", f"/findings/{finding.id}/links/preview?place_id={place_id}"
    )

    assert response.status_code == 200, response.text
    preview = response.json()
    expected = issue_text(
        IssueFacts(
            finding_id=str(finding.id),
            signal="slower",
            ratio=812.0 / 203.0,
            severity="critical",
            since=NOW,
            service="checkout",
            link_back=f"{CONSOLE}/findings/{finding.id}",
        )
    )
    assert (preview["title"], preview["body"]) == (expected.title, expected.markdown)
    assert (preview["place_key"], preview["public"], preview["refusal"]) == (
        "acme/api",
        False,
        None,
    )
    assert await _sync_jobs(db, acme) == 0
    assert (await _fresh(db, finding)).case_state is None


async def test_the_preview_of_a_public_repository_says_it_will_be_refused(
    db: AsyncSession, acme: Team
) -> None:
    _, place_id = await _connected(db, acme, private=False)
    finding = await _case_finding(db, acme)
    response = await _call(
        db, acme, acme.member, "GET", f"/findings/{finding.id}/links/preview?place_id={place_id}"
    )
    assert response.json()["refusal"].startswith("This repository is public.")


# ---------------------------------------------------------------- the click


async def test_the_click_writes_the_link_creating_with_the_preview_s_facts(
    db: AsyncSession, acme: Team
) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)

    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )

    assert response.status_code == 201, response.text
    link = await _link(db, acme, uuid.UUID(response.json()["link_id"]))
    assert (link.role, link.sync_status, link.connection_id, link.place_id) == (
        "synced",
        "creating",
        connection_id,
        place_id,
    )
    preview = await _call(
        db, acme, acme.member, "GET", f"/findings/{finding.id}/links/preview?place_id={place_id}"
    )
    assert link.frozen_facts is not None
    assert issue_text(IssueFacts.from_json(link.frozen_facts)).markdown == preview.json()["body"]
    assert await _sync_jobs(db, acme) == 1
    [shown] = response.json()["finding"]["links"]
    assert (shown["sync_status"], shown["external_key"], shown["url"]) == ("creating", None, None)
    assert [event.kind for event in await _events(db, acme, finding.id)][-1] == "link_added"
    audit = await db.scalar(
        select(AuditLogEntry).where(
            AuditLogEntry.org_id == acme.org_id, AuditLogEntry.action == "link.issue_requested"
        )
    )
    assert audit is not None and audit.actor_user_id == acme.member.id


async def test_a_link_starts_a_case_with_nobody_assigned_and_no_acknowledgement(
    db: AsyncSession, acme: Team
) -> None:
    """AC99: the finding stays in the open tier, unacknowledged, with nobody on it."""
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    assert response.status_code == 201
    row = await _fresh(db, finding)
    assert (row.case_state, row.assignee_id, row.acknowledged_at) == ("investigating", None, None)


async def test_a_link_is_an_act_beyond_taking_so_the_untouched_end_no_longer_applies(
    db: AsyncSession, acme: Team
) -> None:
    """AC90's link half: taken, then a link: `case_worked_at` is set, which the sweep's
    untouched end requires to be null."""
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme, state="closed")
    assert (
        await _call(
            db,
            acme,
            acme.member,
            "PATCH",
            f"/findings/{finding.id}/case",
            {"case_state": "investigating"},
        )
    ).status_code == 200
    assert (await _fresh(db, finding)).case_worked_at is None
    await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    assert (await _fresh(db, finding)).case_worked_at is not None


async def test_a_second_click_is_refused_and_adds_no_job(db: AsyncSession, acme: Team) -> None:
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    path, body = f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    assert (await _call(db, acme, acme.member, "POST", path, body)).status_code == 201
    second = await _call(db, acme, acme.second_member, "POST", path, body)
    assert second.status_code == 409
    assert second.json()["detail"] == "This case already has a synced issue."
    assert await _sync_jobs(db, acme) == 1


@pytest.mark.parametrize(
    ("arrange", "words"),
    [
        ("public", "This repository is public."),
        ("needs_attention", "The GitHub connection needs attention"),
        ("unreachable", "nexovue cannot reach this repository now."),
        ("resolved", "This case is resolved. Reopen it"),
    ],
)
async def test_what_refuses_a_click(db: AsyncSession, acme: Team, arrange: str, words: str) -> None:
    _, place_id = await _connected(
        db,
        acme,
        private=arrange != "public",
        state="needs_attention" if arrange == "needs_attention" else "working",
    )
    if arrange == "unreachable":
        await db.execute(
            text("UPDATE connection_repositories SET is_reachable = false WHERE id = :id"),
            {"id": place_id},
        )
    finding = await _case_finding(
        db,
        acme,
        **(
            {
                "case_state": "resolved",
                "case_opened_at": NOW,
                "case_activity_at": NOW,
                "case_resolved_at": NOW,
                "case_resolution": "resolved",
            }
            if arrange == "resolved"
            else {}
        ),
    )
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    assert response.status_code == 409
    assert response.json()["detail"].startswith(words)
    assert await db.scalar(select(FindingLink.id).where(FindingLink.org_id == acme.org_id)) is None


async def test_an_allowed_public_repository_takes_the_click(db: AsyncSession, acme: Team) -> None:
    _, place_id = await _connected(db, acme, private=False)
    await db.execute(
        text("UPDATE connection_repositories SET public_allowed_at = :now WHERE id = :id"),
        {"now": NOW, "id": place_id},
    )
    finding = await _case_finding(db, acme)
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    assert response.status_code == 201


async def test_another_organization_s_repository_is_the_same_404(
    db: AsyncSession, acme: Team, globex: Team
) -> None:
    _, their_place = await _connected(db, globex)
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    for method, path, body in (
        ("GET", f"/findings/{finding.id}/links/preview?place_id={their_place}", None),
        ("POST", f"/findings/{finding.id}/links", {"place_id": str(their_place)}),
    ):
        response = await _call(db, acme, acme.member, method, path, body)
        assert response.status_code == 404
        assert response.json()["detail"] == "No such repository on this organization's connection."


async def test_a_viewer_cannot_create_retry_or_remove(db: AsyncSession, acme: Team) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id)
    for method, path, body in (
        ("POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}),
        ("POST", f"/findings/{finding.id}/links/{link_id}/retry", None),
        ("DELETE", f"/findings/{finding.id}/links/{link_id}", None),
        ("GET", f"/findings/{finding.id}/links/preview?place_id={place_id}", None),
    ):
        assert (await _call(db, acme, acme.viewer, method, path, body)).status_code == 403


async def test_a_token_reads_no_links(db: AsyncSession, acme: Team) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    await _synced(db, acme, finding, connection_id, place_id)
    response = await _call(db, acme, None, "GET", f"/findings/{finding.id}")
    assert response.status_code == 200
    assert "links" not in response.json()


# --------------------------------------------- what a case act wants (FR38)


async def _active(db: AsyncSession, team: Team, **extra: Any) -> FindingRow:
    return await _case_finding(
        db,
        team,
        state="closed",
        case_state="investigating",
        case_opened_at=NOW,
        case_activity_at=NOW,
        **extra,
    )


async def test_a_resolve_asks_the_issue_to_close_with_one_comment(
    db: AsyncSession, acme: Team
) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id)

    response = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{finding.id}/case", {"case_state": "resolved"}
    )

    assert response.status_code == 200, response.text
    link = await _link(db, acme, link_id)
    assert (link.wanted_state, link.comment_wanted) == ("closed", "resolved")
    assert link.next_sync_at is not None
    assert await _sync_jobs(db, acme) == 1


async def test_resolve_here_leaving_the_issue_open_wants_nothing(
    db: AsyncSession, acme: Team
) -> None:
    """AC92: no wanted state, no comment."""
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id)
    response = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{finding.id}/case",
        {"case_state": "resolved", "leave_issue_open": True},
    )
    assert response.status_code == 200
    link = await _link(db, acme, link_id)
    assert (link.wanted_state, link.comment_wanted) == (None, None)
    assert await _sync_jobs(db, acme) == 0


async def test_resolving_a_held_case_ends_the_hold_and_wants_nothing(
    db: AsyncSession, acme: Team
) -> None:
    """D73: the issue is already closed; nothing more goes there."""
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id, seen="closed", held=True)
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{finding.id}/case", {"case_state": "resolved"}
    )
    link = await _link(db, acme, link_id)
    assert (link.tracker_closed_at, link.wanted_state, link.comment_wanted) == (None, None, None)


async def test_a_false_alarm_on_the_root_closes_as_not_planned(
    db: AsyncSession, acme: Team
) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(
        db, acme, case_state="investigating", case_opened_at=NOW, case_activity_at=NOW
    )
    link_id = await _synced(db, acme, finding, connection_id, place_id)
    response = await _call(db, acme, acme.member, "POST", f"/findings/{finding.id}/false-alarm")
    assert response.status_code == 200, response.text
    link = await _link(db, acme, link_id)
    assert (link.wanted_state, link.comment_wanted) == ("closed", "false_alarm")


@pytest.mark.parametrize(("seen", "wanted"), [("closed", "open"), ("open", None)])
async def test_a_reopen_asks_the_issue_to_reopen_unless_it_was_left_open(
    db: AsyncSession, acme: Team, seen: str, wanted: str | None
) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(
        db,
        acme,
        state="closed",
        case_state="resolved",
        case_opened_at=NOW,
        case_activity_at=NOW,
        case_resolved_at=NOW,
        case_resolution="resolved",
    )
    link_id = await _synced(db, acme, finding, connection_id, place_id, seen=seen)
    response = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{finding.id}/case",
        {"case_state": "investigating"},
    )
    assert response.status_code == 200, response.text
    link = await _link(db, acme, link_id)
    assert (link.wanted_state, link.comment_wanted) == (wanted, None)


# ------------------------------------------------------- retry and remove


async def test_a_retry_makes_a_waiting_link_due_now(db: AsyncSession, acme: Team) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id, status="waiting")
    await db.execute(
        text(
            "UPDATE finding_links SET last_failure = 'server_error', next_sync_at = :later "
            "WHERE id = :id"
        ),
        {"later": datetime.now(UTC) + timedelta(hours=1), "id": link_id},
    )
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links/{link_id}/retry"
    )
    assert response.status_code == 202, response.text
    due = (await _link(db, acme, link_id)).next_sync_at
    assert due is not None and due <= datetime.now(UTC)
    assert await _sync_jobs(db, acme) == 1


async def test_a_retry_where_it_cannot_help_is_refused(db: AsyncSession, acme: Team) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id)
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links/{link_id}/retry"
    )
    assert response.status_code == 409


async def test_removing_a_link_keeps_the_issue_and_frees_the_slot(
    db: AsyncSession, acme: Team
) -> None:
    connection_id, place_id = await _connected(db, acme)
    finding = await _active(db, acme)
    link_id = await _synced(db, acme, finding, connection_id, place_id)
    response = await _call(
        db, acme, acme.member, "DELETE", f"/findings/{finding.id}/links/{link_id}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["links"] == []
    assert [event.kind for event in await _events(db, acme, finding.id)][-1] == "link_removed"
    created = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    assert created.status_code == 201


async def test_a_link_being_created_cannot_be_removed(db: AsyncSession, acme: Team) -> None:
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    created = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/links", {"place_id": str(place_id)}
    )
    link_id = created.json()["link_id"]
    response = await _call(
        db, acme, acme.member, "DELETE", f"/findings/{finding.id}/links/{link_id}"
    )
    assert response.status_code == 409


# -------------------------------------------------------------- disconnect


async def test_a_disconnect_stops_every_link_and_frees_each_case_s_slot(
    db: AsyncSession, acme: Team
) -> None:
    """FR32, AC37: in the disconnect's own transaction, and the screen's count was real."""
    connection_id, place_id = await _connected(db, acme)
    first, second = await _active(db, acme), await _active(db, acme, service="payments")
    held = await _synced(db, acme, first, connection_id, place_id, seen="closed", held=True)
    await db.execute(
        text("UPDATE finding_links SET wanted_state = 'open' WHERE id = :id"), {"id": held}
    )
    other = FindingLink(
        org_id=acme.org_id,
        finding_id=second.id,
        connection_id=connection_id,
        place_id=place_id,
        role="synced",
        sync_status="in_sync",
        external_id="5000002",
        external_key="acme/api#2",
        seen_state="open",
    )
    db.add(other)
    await db.flush()
    from nexovue_api.connections.schemas import Availability

    overview = await connections.overview(
        db,
        org_id=acme.org_id,
        support_view=False,
        availability=Availability(deployment="self-hosted", enabled=True, app_base_url=CONSOLE),
        email_available=False,
    )
    assert [view.synced_links for view in overview.connections] == [2]

    await connections.disconnect(
        db, org_id=acme.org_id, actor_user_id=acme.owner.id, connection_id=connection_id, hooks=()
    )

    for link_id in (held, other.id):
        link = await _link(db, acme, link_id)
        assert (link.sync_status, link.next_sync_at, link.wanted_state, link.tracker_closed_at) == (
            "stopped",
            None,
            None,
            None,
        )
        assert link.external_key is not None
    _, new_place = await _connected(db, acme)
    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{first.id}/links", {"place_id": str(new_place)}
    )
    assert response.status_code == 201


# ------------------------------------------- pasted references and promotion (T6.1)

ISSUE_ADDRESS: Final = "https://github.com/ACME/Api/issues/7"


async def _attach(
    db: AsyncSession, team: Team, finding: FindingRow, url: str = ISSUE_ADDRESS
) -> httpx.Response:
    return await _call(db, team, team.member, "POST", f"/findings/{finding.id}/links", {"url": url})


async def _read_once(db: AsyncSession, link_id: uuid.UUID, external_id: str = "7000007") -> None:
    """What the job's first read of a reference leaves: the tracker's id, and a status."""
    await db.execute(
        text(
            "UPDATE finding_links SET external_id = :id, seen_state = 'open', "
            "last_synced_at = now() WHERE id = :link"
        ),
        {"id": external_id, "link": link_id},
    )


async def _audit_actions(db: AsyncSession, team: Team, prefix: str) -> list[str]:
    return list(
        await db.scalars(
            select(AuditLogEntry.action)
            .where(AuditLogEntry.org_id == team.org_id, AuditLogEntry.action.like(f"{prefix}%"))
            .order_by(AuditLogEntry.id)
        )
    )


async def test_a_pasted_address_attaches_as_a_reference_read_by_the_job(
    db: AsyncSession, acme: Team
) -> None:
    """FR40, FR41: parsed to the place's own spelling, attached as a reference, read by a
    job; it starts a case with nobody assigned, as any link does (D70)."""
    connection_id, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)

    response = await _attach(db, acme, finding)

    assert response.status_code == 201, response.text
    link = await _link(db, acme, uuid.UUID(response.json()["link_id"]))
    assert (
        link.role,
        link.sync_status,
        link.external_key,
        link.external_id,
        link.connection_id,
        link.place_id,
    ) == ("reference", "in_sync", "acme/api#7", None, connection_id, place_id)
    assert link.next_sync_at is not None
    assert await _sync_jobs(db, acme) == 1
    [shown] = response.json()["finding"]["links"]
    assert (shown["role"], shown["url"], shown["place_public"]) == (
        "reference",
        "https://github.com/acme/api/issues/7",
        False,
    )
    row = await _fresh(db, finding)
    assert (row.case_state, row.assignee_id, row.case_worked_at is not None) == (
        "investigating",
        None,
        True,
    )
    assert [event.kind for event in await _events(db, acme, finding.id)][-1] == "link_added"
    assert await _audit_actions(db, acme, "link.") == ["link.added"]


@pytest.mark.parametrize(
    ("url", "status", "words"),
    [
        ("https://github.com/acme/api/pull/7", 422, "not a GitHub issue address"),
        ("https://evil.example/acme/api/issues/7", 422, "not a GitHub issue address"),
        ("https://github.com/acme/other/issues/7", 409, "not in a repository"),
        ("https://github.com/elsewhere/api/issues/7", 409, "not in a repository"),
    ],
)
async def test_an_address_outside_a_connected_repository_is_refused_and_nothing_is_added(
    db: AsyncSession, acme: Team, url: str, status: int, words: str
) -> None:
    """AC54: refused here, by parsing and a lookup; the API holds no tracker client at all
    (`test_no_tracker_calls_in_api.py`), so no call can go out."""
    await _connected(db, acme)
    finding = await _case_finding(db, acme)

    response = await _attach(db, acme, finding, url)

    assert response.status_code == status, response.text
    assert words in response.text
    count = await db.scalar(
        select(func.count()).select_from(FindingLink).where(FindingLink.org_id == acme.org_id)
    )
    assert (count, await _sync_jobs(db, acme)) == (0, 0)
    assert (await _fresh(db, finding)).case_state is None


async def test_a_repository_the_connection_no_longer_reaches_takes_no_reference(
    db: AsyncSession, acme: Team
) -> None:
    _, place_id = await _connected(db, acme)
    await db.execute(
        text("UPDATE connection_repositories SET is_reachable = false WHERE id = :id"),
        {"id": place_id},
    )
    finding = await _case_finding(db, acme)

    assert (await _attach(db, acme, finding)).status_code == 409


async def test_one_issue_is_a_reference_on_two_cases_but_once_on_each(
    db: AsyncSession, acme: Team
) -> None:
    """AC53: the same issue may be attached to many cases, once each."""
    await _connected(db, acme)
    first = await _case_finding(db, acme)
    second = await _case_finding(db, acme, service="payments")

    assert (await _attach(db, acme, first)).status_code == 201
    assert (await _attach(db, acme, second)).status_code == 201
    again = await _attach(db, acme, first, "acme/api#7")

    assert again.status_code == 409
    assert "already on this case" in again.text


async def test_a_case_holds_at_most_twenty_references(db: AsyncSession, acme: Team) -> None:
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    for number in range(1, 21):
        response = await _attach(db, acme, finding, f"acme/api#{number}")
        assert response.status_code == 201, response.text

    refused = await _attach(db, acme, finding, "acme/api#21")

    assert refused.status_code == 409
    assert "at most 20" in refused.text


async def test_a_reference_on_a_resolved_case_leaves_it_resolved(
    db: AsyncSession, acme: Team
) -> None:
    """Like a note: shown on the case, which it never changes (FR41)."""
    await _connected(db, acme)
    finding = await _case_finding(
        db,
        acme,
        state="closed",
        case_state="resolved",
        case_resolution="resolved",
        case_opened_at=NOW,
        case_resolved_at=NOW,
        case_activity_at=NOW,
    )

    assert (await _attach(db, acme, finding)).status_code == 201
    assert (await _fresh(db, finding)).case_state == "resolved"


async def _promote(
    db: AsyncSession, team: Team, finding: FindingRow, link_id: uuid.UUID
) -> httpx.Response:
    return await _call(
        db, team, team.member, "POST", f"/findings/{finding.id}/links/{link_id}/promote"
    )


async def test_promoting_a_read_reference_makes_it_the_synced_issue_through_a_job(
    db: AsyncSession, acme: Team
) -> None:
    """FR41, AC88: a second choice, run as a job; the API only marks it `promoting`."""
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = uuid.UUID((await _attach(db, acme, finding)).json()["link_id"])
    await _read_once(db, link_id)

    response = await _promote(db, acme, finding, link_id)

    assert response.status_code == 202, response.text
    link = await _link(db, acme, link_id)
    assert (link.role, link.sync_status, link.external_id) == ("synced", "promoting", "7000007")
    assert await _sync_jobs(db, acme) == 1
    assert [event.kind for event in await _events(db, acme, finding.id)][-1] == "link_promoted"
    assert await _audit_actions(db, acme, "link.") == ["link.added", "link.promoted"]


async def test_a_reference_not_yet_read_cannot_be_promoted(db: AsyncSession, acme: Team) -> None:
    """Until its first read, nexovue does not know the issue's id, and so cannot hold it to
    one case; the person is told to wait."""
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = uuid.UUID((await _attach(db, acme, finding)).json()["link_id"])

    response = await _promote(db, acme, finding, link_id)

    assert response.status_code == 409
    assert "not read this issue yet" in response.text
    assert (await _link(db, acme, link_id)).role == "reference"


async def test_an_issue_is_synced_on_one_case_only(db: AsyncSession, acme: Team) -> None:
    """AC53: promoting an issue synced on another case is refused, and so is promoting a
    second issue onto a case that has one."""
    await _connected(db, acme)
    first = await _case_finding(db, acme)
    second = await _case_finding(db, acme, service="payments")
    on_first = uuid.UUID((await _attach(db, acme, first)).json()["link_id"])
    on_second = uuid.UUID((await _attach(db, acme, second)).json()["link_id"])
    await _read_once(db, on_first)
    await _read_once(db, on_second)
    assert (await _promote(db, acme, first, on_first)).status_code == 202

    elsewhere = await _promote(db, acme, second, on_second)
    assert elsewhere.status_code == 409
    assert "synced with another case" in elsewhere.text

    other = uuid.UUID((await _attach(db, acme, first, "acme/api#8")).json()["link_id"])
    await _read_once(db, other, external_id="7000008")
    already = await _promote(db, acme, first, other)
    assert already.status_code == 409
    assert "already has a synced issue" in already.text


@pytest.mark.parametrize(
    ("arrange", "words"),
    [
        ("public", "This repository is public"),
        ("resolved", "This case is resolved"),
        ("needs_attention", "needs attention"),
        ("synced", "Only an attached issue"),
    ],
)
async def test_what_refuses_a_promotion(
    db: AsyncSession, acme: Team, arrange: str, words: str
) -> None:
    connection_id, _ = await _connected(db, acme, private=arrange != "public")
    finding = await _case_finding(db, acme)
    link_id = uuid.UUID((await _attach(db, acme, finding)).json()["link_id"])
    await _read_once(db, link_id)
    if arrange == "resolved":
        finding.case_state, finding.case_resolution = "resolved", "resolved"
        finding.case_resolved_at = NOW
        await db.flush()
    elif arrange == "needs_attention":
        await db.execute(
            text("UPDATE connections SET state = 'needs_attention' WHERE id = :id"),
            {"id": connection_id},
        )
    elif arrange == "synced":
        await db.execute(
            text("UPDATE finding_links SET role = 'synced' WHERE id = :id"), {"id": link_id}
        )

    response = await _promote(db, acme, finding, link_id)

    assert response.status_code == 409, response.text
    assert words in response.text


async def test_promoting_another_organization_s_link_is_the_same_404(
    db: AsyncSession, acme: Team, globex: Team
) -> None:
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = uuid.UUID((await _attach(db, acme, finding)).json()["link_id"])
    theirs = await _case_finding(db, globex)

    response = await _call(
        db, globex, globex.member, "POST", f"/findings/{theirs.id}/links/{link_id}/promote"
    )

    assert response.status_code == 404


async def test_a_link_in_a_repository_that_is_public_says_so(db: AsyncSession, acme: Team) -> None:
    """FR42, AC115: once the refresh records a repository as public, its links show it."""
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)
    await _attach(db, acme, finding)
    await db.execute(
        text("UPDATE connection_repositories SET is_private = false WHERE id = :id"),
        {"id": place_id},
    )

    response = await _call(db, acme, acme.member, "GET", f"/findings/{finding.id}")

    [shown] = response.json()["links"]
    assert shown["place_public"] is True


async def test_a_request_names_either_a_repository_or_an_address(
    db: AsyncSession, acme: Team
) -> None:
    _, place_id = await _connected(db, acme)
    finding = await _case_finding(db, acme)

    for body in ({}, {"place_id": str(place_id), "url": ISSUE_ADDRESS}):
        response = await _call(db, acme, acme.member, "POST", f"/findings/{finding.id}/links", body)
        assert response.status_code == 422, response.text
