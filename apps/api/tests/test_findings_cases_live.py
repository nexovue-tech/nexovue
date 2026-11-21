"""A person works a case on a finding, against real Postgres (F22, plan T1.5, T1.7
to T1.9; AC2, AC3, AC6, AC7, AC9, AC11, AC14, AC16, AC19 to AC22, AC61, AC62,
AC69, AC102, AC111, AC117, and T1.2's direct-insert refusals).

ClickHouse is deliberately absent: the tenant repository is a stub that *refuses
to answer before the case transaction has committed*, which is AC102's proof —
no ClickHouse read may run while a case operation holds a finding's lock.

Postgres rows are written under a provisioned tenant inside one outer
transaction per test, rolled back afterwards.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from nexovue_platform.cases import RECORD_FINDING_EVENT_SQL, finding_event_parameters
from nexovue_platform.email import EmailError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    FindingEvent,
    FindingNote,
    User,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.deps import get_app_settings, get_db_session, get_optional_email_sender
from nexovue_api.members import service as members
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
SERVICE: Final = "checkout"
NOW: Final = datetime(2026, 11, 2, 14, 2, tzinfo=UTC)


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
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="apps/api/tests/test_findings_cases_live.py",
    remedy="Postgres is not reachable — run `just dev`",
)


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


@dataclass(frozen=True)
class Team:
    tenant: ProvisionedTenant
    owner: User
    admin: User
    member: User
    second_member: User
    viewer: User
    pending: User

    @property
    def org_id(self) -> uuid.UUID:
        return self.tenant.organization.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.tenant.project.id


async def _team(db: AsyncSession, name: str) -> Team:
    tenant = await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    people: dict[str, User] = {}
    for label, role, joined in (
        ("admin", "admin", True),
        ("member", "member", True),
        ("second_member", "member", True),
        ("viewer", "viewer", True),
        ("pending", "member", False),
    ):
        person = User(
            org_id=tenant.organization.id,
            email=f"{label}-{uuid.uuid4()}@example.test",
            name=label.replace("_", " ").capitalize(),
            role=role,
            password_hash="x" if joined else None,
        )
        db.add(person)
        people[label] = person
    await db.flush()
    return Team(tenant=tenant, owner=tenant.user, **people)


@pytest.fixture
async def acme(db: AsyncSession) -> Team:
    return await _team(db, "Acme")


@pytest.fixture
async def globex(db: AsyncSession) -> Team:
    return await _team(db, "Globex")


async def _finding(
    db: AsyncSession,
    team: Team,
    *,
    state: str = "open",
    service: str | None = SERVICE,
    scope: str = "service",
    **extra: Any,
) -> FindingRow:
    row = FindingRow(
        org_id=team.org_id,
        project_id=team.project_id,
        scope=scope,
        service_name=service,
        severity=extra.pop("severity", "warning"),
        state=state,
        closed_reason="recovered" if state == "closed" else None,
        title=f"{service or 'this project'} is slower than normal",
        signals=[],
        started_at=NOW,
        closed_at=NOW if state == "closed" else None,
        **extra,
    )
    db.add(row)
    await db.flush()
    return row


class CommitWitness:
    """AC102's proof: the stub repository refuses to answer until the case
    transaction has committed, so a ClickHouse read under a lock fails loudly."""

    def __init__(self) -> None:
        self.committed = False

    async def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[Any]:
        assert self.committed, "a ClickHouse read ran before the case transaction committed (AC102)"
        return []


@dataclass
class RecordingEmail:
    """What FR5's one email carried, or a sender that refuses (AC11)."""

    failing: bool = False
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, *, to: str, message: Any, data: dict[str, Any], **_: Any) -> None:
        if self.failing:
            raise EmailError("refused on purpose")
        self.sent.append({"to": to, "message": message, "data": data})


def _client(
    db: AsyncSession,
    team: Team,
    person: User | None,
    *,
    email: RecordingEmail | None = None,
    support_view: bool = False,
) -> tuple[httpx.AsyncClient, CommitWitness]:
    """As `person`, as a token scoped to the team's project when None, or as a
    support view of `person`."""
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)
    organization = team.tenant.organization
    impersonation = (
        Impersonation(
            impersonation_id=str(uuid.uuid4()),
            operator_label="Raj at nexovue",
            reason="test",
            grant_id=str(uuid.uuid4()),
            until=datetime(2100, 1, 1, tzinfo=UTC),
        )
        if support_view
        else None
    )
    reader = (
        Reader(org_id=organization.id, project_scope=team.project_id, token_id=uuid.uuid4())
        if person is None
        else Reader(
            org_id=organization.id,
            identity=Identity(user=person, organization=organization, impersonation=impersonation),
        )
    )

    witness = CommitWitness()
    original_commit = db.commit

    async def commit_and_tell() -> None:
        witness.committed = True
        await original_commit()

    db.commit = commit_and_tell  # type: ignore[method-assign]

    async def db_override() -> AsyncIterator[AsyncSession]:
        witness.committed = False
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    if person is not None:
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            user=person, organization=organization, impersonation=impersonation
        )
    app.dependency_overrides[get_tenant_repository] = lambda: witness
    app.dependency_overrides[get_optional_email_sender] = lambda: email
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    return client, witness


async def _call(
    db: AsyncSession,
    team: Team,
    person: User | None,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    email: RecordingEmail | None = None,
    support_view: bool = False,
) -> httpx.Response:
    client, _ = _client(db, team, person, email=email, support_view=support_view)
    async with client:
        return await client.request(method, f"{API_PREFIX}{path}", json=body)


async def _events(
    db: AsyncSession, team: Team, finding_id: uuid.UUID, kind: str | None = None
) -> list[FindingEvent]:
    statement = (
        select(FindingEvent)
        .where(FindingEvent.org_id == team.org_id, FindingEvent.finding_id == finding_id)
        .order_by(FindingEvent.at, FindingEvent.id)
    )
    if kind is not None:
        statement = statement.where(FindingEvent.kind == kind)
    return list(await db.scalars(statement))


async def _fresh(db: AsyncSession, row: FindingRow) -> FindingRow:
    await db.refresh(row)
    return row


# ----------------------------------------------------- the schema's refusals


async def test_a_cross_organization_assignee_is_refused_by_the_database(
    db: AsyncSession, acme: Team, globex: Team
) -> None:
    """T1.2, AC72: the composite key, not application code, is what refuses."""
    row = await _finding(db, acme)
    row.case_state, row.case_opened_at, row.case_activity_at = "investigating", NOW, NOW
    row.assignee_id = globex.member.id
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_a_cross_organization_event_actor_is_refused(
    db: AsyncSession, acme: Team, globex: Team
) -> None:
    row = await _finding(db, acme)
    with pytest.raises(IntegrityError):
        await db.execute(
            text(RECORD_FINDING_EVENT_SQL),
            finding_event_parameters(
                "case_started",
                org_id=acme.org_id,
                finding_id=row.id,
                actor_kind="person",
                actor_id=globex.member.id,
                at=NOW,
            ),
        )
    await db.rollback()


async def test_a_cross_organization_note_author_is_refused(
    db: AsyncSession, acme: Team, globex: Team
) -> None:
    row = await _finding(db, acme)
    db.add(FindingNote(org_id=acme.org_id, finding_id=row.id, author_id=globex.member.id, body="x"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_a_cross_project_root_is_refused(db: AsyncSession, acme: Team) -> None:
    from nexovue_api.data.postgres.models import Project

    other = Project(org_id=acme.org_id, name="staging", slug=f"staging-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()
    root = await _finding(db, acme)
    stray = FindingRow(
        org_id=acme.org_id,
        project_id=other.id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        state="open",
        title="x",
        signals=[],
        started_at=NOW,
        root_finding_id=root.id,
    )
    db.add(stray)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_the_database_refuses_a_second_active_case_on_one_service(
    db: AsyncSession, acme: Team
) -> None:
    """AC14's database half: the partial unique index is the arbiter (D21)."""
    first = await _finding(db, acme, state="closed")
    first.case_state, first.case_opened_at, first.case_activity_at = "investigating", NOW, NOW
    await db.flush()
    second = await _finding(db, acme, state="open")
    second.case_state, second.case_opened_at, second.case_activity_at = "investigating", NOW, NOW
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ----------------------------------------------------------------- lifecycle


async def test_taking_a_recovered_finding_is_one_click_and_no_field(
    db: AsyncSession, acme: Team
) -> None:
    """AC2, and AC102 throughout: the view is built only after the commit."""
    row = await _finding(db, acme, state="closed")

    answer = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["email_outcome"] == "none"
    assert body["finding"]["case_state"] == "investigating"
    assert body["finding"]["assignee_id"] == str(acme.member.id)
    assert body["finding"]["case_signal"] == "closed"
    row = await _fresh(db, row)
    assert row.case_state == "investigating" and row.assignee_id == acme.member.id
    assert [event.kind for event in await _events(db, acme, row.id)] == [
        "case_started",
        "assigned",
    ]


async def test_taking_an_open_unacknowledged_finding_also_acknowledges_it(
    db: AsyncSession, acme: Team
) -> None:
    """AC3's first half."""
    row = await _finding(db, acme, state="open")

    answer = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    assert answer.status_code == 200, answer.text
    row = await _fresh(db, row)
    assert row.acknowledged_by == acme.member.id
    kinds = {event.kind for event in await _events(db, acme, row.id)}
    assert "acknowledged" in kinds


async def test_acknowledging_starts_the_case_and_assigns_the_person(
    db: AsyncSession, acme: Team
) -> None:
    """AC1 (FR3): the existing route stays, and now makes it theirs."""
    row = await _finding(db, acme, state="open")

    answer = await _call(db, acme, acme.member, "POST", f"/findings/{row.id}/acknowledge")

    assert answer.status_code == 200, answer.text
    row = await _fresh(db, row)
    assert row.case_state == "investigating"
    assert row.assignee_id == acme.member.id
    assert row.acknowledged_by == acme.member.id


async def test_two_people_taking_one_case_gives_one_success_and_one_409(
    db: AsyncSession, acme: Team
) -> None:
    """AC6: the second sees who has it."""
    row = await _finding(db, acme, state="open")
    first = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )
    second = await _call(
        db, acme, acme.admin, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "Member" in second.json()["detail"]
    row = await _fresh(db, row)
    assert row.assignee_id == acme.member.id


async def test_a_note_starts_a_case_with_nobody_assigned_and_no_acknowledgement(
    db: AsyncSession, acme: Team
) -> None:
    """AC3's second half, and AC99's server half (D70)."""
    row = await _finding(db, acme, state="open")

    answer = await _call(
        db, acme, acme.member, "POST", f"/findings/{row.id}/notes", {"body": "seeing this too"}
    )

    assert answer.status_code == 200, answer.text
    row = await _fresh(db, row)
    assert row.case_state == "investigating"
    assert row.assignee_id is None
    assert row.acknowledged_at is None
    assert row.case_worked_at is not None


async def test_resolving_and_reopening_each_write_one_event_naming_the_person(
    db: AsyncSession, acme: Team
) -> None:
    """AC7, and the audit rows beside them."""
    row = await _finding(db, acme, state="closed")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    resolved = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "resolved"}
    )
    assert resolved.status_code == 200, resolved.text
    row = await _fresh(db, row)
    assert row.case_state == "resolved" and row.case_resolution == "resolved"
    assert row.case_resolved_by == acme.member.id

    reopened = await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )
    assert reopened.status_code == 200, reopened.text
    row = await _fresh(db, row)
    assert row.case_state == "investigating"

    resolved_events = await _events(db, acme, row.id, "case_resolved")
    reopened_events = await _events(db, acme, row.id, "case_reopened")
    assert len(resolved_events) == 1 and resolved_events[0].actor_id == acme.member.id
    assert len(reopened_events) == 1 and reopened_events[0].actor_id == acme.member.id
    audited = await db.scalars(
        select(AuditLogEntry.action).where(
            AuditLogEntry.org_id == acme.org_id,
            AuditLogEntry.target == f"finding:{row.id}",
        )
    )
    assert {"case.started", "case.resolved", "case.reopened"} <= set(audited)
    # AC65: a case is not a second alarm — none of this wrote a notification row.
    notifications = await db.scalar(
        text("SELECT count(*) FROM notifications WHERE org_id = :org_id"),
        {"org_id": acme.org_id},
    )
    assert notifications == 0


async def test_taking_a_closed_finding_brings_in_the_services_open_finding(
    db: AsyncSession, acme: Team
) -> None:
    """AC111's first half (FR11): the open finding joins the new case."""
    closed = await _finding(db, acme, state="closed")
    open_row = await _finding(db, acme, state="open")

    answer = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{closed.id}/case",
        {"case_state": "investigating"},
    )

    assert answer.status_code == 200, answer.text
    open_row = await _fresh(db, open_row)
    assert open_row.root_finding_id == closed.id
    assert open_row.case_state is None
    body = answer.json()["finding"]
    assert body["case_signal"] == "open"
    assert body["open_joined_finding_id"] == str(open_row.id)


async def test_taking_a_closed_finding_joins_the_open_findings_active_case(
    db: AsyncSession, acme: Team
) -> None:
    """AC111's second half, and AC14's action half: the index refuses, the act joins."""
    open_row = await _finding(db, acme, state="open")
    taken = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{open_row.id}/case",
        {"case_state": "investigating"},
    )
    assert taken.status_code == 200
    closed = await _finding(db, acme, state="closed")

    answer = await _call(
        db,
        acme,
        acme.admin,
        "PATCH",
        f"/findings/{closed.id}/case",
        {"case_state": "investigating"},
    )

    assert answer.status_code == 200, answer.text
    closed = await _fresh(db, closed)
    assert closed.case_state is None
    assert closed.root_finding_id == open_row.id
    # The answer is the case that holds the service, which keeps its assignee.
    assert answer.json()["finding"]["id"] == str(open_row.id)
    assert answer.json()["finding"]["assignee_id"] == str(acme.member.id)


async def test_taking_a_finding_beside_a_quiet_case_joins_it_and_restarts_its_clock(
    db: AsyncSession, acme: Team
) -> None:
    """AC68's act half (design, *Joining*): a finding that opened past the 7-day
    window stands alone; taking it joins that quiet case, and the act is a
    person's act on that case, so its idle clock starts again."""
    quiet_since = datetime.now(UTC) - timedelta(days=12)
    quiet = await _finding(
        db,
        acme,
        state="closed",
        case_state="investigating",
        case_opened_at=quiet_since,
        case_activity_at=quiet_since,
        assignee_id=acme.member.id,
    )
    standalone = await _finding(db, acme, state="open")

    taken = await _call(
        db,
        acme,
        acme.admin,
        "PATCH",
        f"/findings/{standalone.id}/case",
        {"case_state": "investigating"},
    )

    assert taken.status_code == 200, taken.text
    standalone = await _fresh(db, standalone)
    quiet = await _fresh(db, quiet)
    assert standalone.root_finding_id == quiet.id and standalone.case_state is None
    assert quiet.assignee_id == acme.member.id
    assert quiet.case_activity_at is not None
    assert quiet.case_activity_at > quiet_since + timedelta(days=11)


async def test_acting_on_a_joined_finding_changes_the_root_and_never_its_own_case(
    db: AsyncSession, acme: Team
) -> None:
    """AC16, AC61: a note from the joined page lands on the root's timeline."""
    root = await _finding(db, acme, state="closed")
    joined = await _finding(db, acme, state="open", root_finding_id=root.id)
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{root.id}/case", {"case_state": "investigating"}
    )

    note = await _call(
        db, acme, acme.second_member, "POST", f"/findings/{joined.id}/notes", {"body": "from here"}
    )
    assert note.status_code == 200, note.text
    joined = await _fresh(db, joined)
    assert joined.case_state is None
    stored = (await db.scalars(select(FindingNote).where(FindingNote.org_id == acme.org_id))).all()
    assert [one.finding_id for one in stored] == [root.id]
    assert (await _events(db, acme, root.id, "note_added")) != []

    resolve = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{joined.id}/case",
        {"case_state": "resolved"},
    )
    assert resolve.status_code == 200
    root = await _fresh(db, root)
    joined = await _fresh(db, joined)
    assert root.case_state == "resolved"
    # D44: the open joined finding left the case in that transaction.
    assert joined.root_finding_id is None
    assert (await _events(db, acme, root.id, "finding_left_case")) != []


async def test_a_note_on_a_resolved_case_leaves_it_resolved(db: AsyncSession, acme: Team) -> None:
    """AC61's second half (FR14)."""
    row = await _finding(db, acme, state="closed")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "resolved"}
    )

    answer = await _call(
        db, acme, acme.member, "POST", f"/findings/{row.id}/notes", {"body": "post-mortem line"}
    )

    assert answer.status_code == 200, answer.text
    row = await _fresh(db, row)
    assert row.case_state == "resolved"


async def test_who_cannot_be_assigned(db: AsyncSession, acme: Team, globex: Team) -> None:
    """AC62: a viewer, a pending invitee, a removed person, another organization's."""
    row = await _finding(db, acme, state="open")
    removed = acme.second_member
    await members.scrub(db, member=removed, now=NOW)
    for wrong in (acme.viewer.id, acme.pending.id, removed.id, globex.member.id):
        answer = await _call(
            db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"assignee_id": str(wrong)}
        )
        assert answer.status_code == 422, f"{wrong}: {answer.status_code}"
    row = await _fresh(db, row)
    assert row.case_state is None


async def test_an_assignment_carries_what_the_page_last_showed(
    db: AsyncSession, acme: Team
) -> None:
    """FR4: a takeover names who it takes over from, and a stale page gets 409."""
    row = await _finding(db, acme, state="open")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    stale = await _call(
        db,
        acme,
        acme.admin,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.admin.id), "expected_assignee_id": None},
    )
    assert stale.status_code == 409

    fresh = await _call(
        db,
        acme,
        acme.admin,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.admin.id), "expected_assignee_id": str(acme.member.id)},
    )
    assert fresh.status_code == 200, fresh.text
    row = await _fresh(db, row)
    assert row.assignee_id == acme.admin.id
    assert row.case_worked_at is not None


# ----------------------------------------------------------------- the email


async def test_an_assignment_by_someone_else_sends_one_plain_email(
    db: AsyncSession, acme: Team
) -> None:
    """AC11, AC117: no title or note, the service in strict form, and the limit."""
    sender = RecordingEmail()
    row = await _finding(db, acme, state="open", service="checkout.internal")

    answer = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.second_member.id)},
        email=sender,
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["email_outcome"] == "will_try"
    assert len(sender.sent) == 1
    [mail] = sender.sent
    assert mail["to"] == acme.second_member.email
    # D28, D39: the strict form cannot read as a domain, and nothing quoted goes.
    assert mail["data"]["service"] == "checkout_internal"
    assert mail["data"]["severity"] == "warning"
    assert "title" not in mail["data"] and "note" not in mail["data"]

    again = await _call(
        db,
        acme,
        acme.admin,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.second_member.id)},
        email=sender,
    )
    assert again.status_code == 200
    assert again.json()["email_outcome"] == "rate_limited"
    assert len(sender.sent) == 1


async def test_assigning_yourself_sends_no_email(db: AsyncSession, acme: Team) -> None:
    sender = RecordingEmail()
    row = await _finding(db, acme, state="open")

    answer = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.member.id)},
        email=sender,
    )

    assert answer.status_code == 200
    assert answer.json()["email_outcome"] == "none"
    assert sender.sent == []


async def test_a_failed_send_does_not_fail_the_assignment(db: AsyncSession, acme: Team) -> None:
    sender = RecordingEmail(failing=True)
    row = await _finding(db, acme, state="open")

    answer = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.second_member.id)},
        email=sender,
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["email_outcome"] == "will_try"
    row = await _fresh(db, row)
    assert row.assignee_id == acme.second_member.id


async def test_with_no_sender_the_outcome_says_so(db: AsyncSession, acme: Team) -> None:
    row = await _finding(db, acme, state="open")
    answer = await _call(
        db,
        acme,
        acme.member,
        "PATCH",
        f"/findings/{row.id}/case",
        {"assignee_id": str(acme.second_member.id)},
    )
    assert answer.json()["email_outcome"] == "not_configured"


# --------------------------------------------------------------------- notes


async def test_a_deleted_notes_text_is_gone_and_the_timeline_keeps_the_fact(
    db: AsyncSession, acme: Team
) -> None:
    """AC19."""
    row = await _finding(db, acme, state="open")
    written = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/findings/{row.id}/notes",
        {"body": "db password is hunter2"},
    )
    note_id = written.json()["note"]["id"]

    deleted = await _call(db, acme, acme.member, "DELETE", f"/findings/{row.id}/notes/{note_id}")

    assert deleted.status_code == 200, deleted.text
    remaining = (
        await db.scalars(select(FindingNote).where(FindingNote.org_id == acme.org_id))
    ).all()
    assert remaining == []
    [event] = await _events(db, acme, row.id, "note_deleted")
    assert event.detail == {"note_id": note_id}


async def test_who_may_delete_which_note(db: AsyncSession, acme: Team) -> None:
    """AC20, AC25: the author or an admin; an author made a viewer still may."""
    row = await _finding(db, acme, state="open")

    async def write_note(author: User, body: str) -> str:
        answer = await _call(db, acme, author, "POST", f"/findings/{row.id}/notes", {"body": body})
        assert answer.status_code == 200, answer.text
        note_id: str = answer.json()["note"]["id"]
        return note_id

    note = await write_note(acme.member, "mine")
    others = await _call(db, acme, acme.second_member, "DELETE", f"/findings/{row.id}/notes/{note}")
    assert others.status_code == 403
    own = await _call(db, acme, acme.member, "DELETE", f"/findings/{row.id}/notes/{note}")
    assert own.status_code == 200

    note = await write_note(acme.member, "for the admin")
    by_admin = await _call(db, acme, acme.admin, "DELETE", f"/findings/{row.id}/notes/{note}")
    assert by_admin.status_code == 200

    note = await write_note(acme.member, "before the demotion")
    acme.member.role = "viewer"
    await db.flush()
    as_viewer = await _call(db, acme, acme.member, "DELETE", f"/findings/{row.id}/notes/{note}")
    assert as_viewer.status_code == 200


async def test_a_viewer_reads_everything_and_changes_nothing(db: AsyncSession, acme: Team) -> None:
    """AC9: every case field, no action; 403 on the write routes."""
    row = await _finding(db, acme, state="open")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    read = await _call(db, acme, acme.viewer, "GET", f"/findings/{row.id}")
    assert read.status_code == 200
    assert read.json()["assignee_id"] == str(acme.member.id)

    forbidden = await _call(
        db, acme, acme.viewer, "PATCH", f"/findings/{row.id}/case", {"case_state": "resolved"}
    )
    assert forbidden.status_code == 403
    no_note = await _call(db, acme, acme.viewer, "POST", f"/findings/{row.id}/notes", {"body": "x"})
    assert no_note.status_code == 403


# ------------------------------------------------------------------ timeline


async def test_the_timeline_merges_the_root_and_its_joined_findings_and_pages(
    db: AsyncSession, acme: Team
) -> None:
    """AC21: newest first, and correct past 100 rows."""
    root = await _finding(db, acme, state="closed")
    joined = await _finding(db, acme, state="open", root_finding_id=root.id)
    for index in range(105):
        await db.execute(
            text(RECORD_FINDING_EVENT_SQL),
            finding_event_parameters(
                "signal_joined",
                org_id=acme.org_id,
                finding_id=joined.id if index % 2 else root.id,
                actor_kind="system",
                at=NOW.replace(minute=index % 60, hour=index // 60),
                signal_count=index + 1,
            ),
        )

    first = await _call(db, acme, acme.member, "GET", f"/findings/{root.id}/timeline")
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["events"]) == 100
    assert page["next_before"] is not None
    ats = [event["at"] for event in page["events"]]
    assert ats == sorted(ats, reverse=True)

    second = await _call(
        db,
        acme,
        acme.member,
        "GET",
        f"/findings/{root.id}/timeline?before={page['next_before']}",
    )
    assert second.status_code == 200
    rest = second.json()
    assert len(rest["events"]) == 5
    assert rest["next_before"] is None
    seen = {event["id"] for event in page["events"]} | {event["id"] for event in rest["events"]}
    assert len(seen) == 105


async def test_a_token_reads_kinds_and_times_only_and_its_own_project(
    db: AsyncSession, acme: Team
) -> None:
    """AC22."""
    row = await _finding(db, acme, state="open")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    timeline = await _call(db, acme, None, "GET", f"/findings/{row.id}/timeline")
    assert timeline.status_code == 200
    for event in timeline.json()["events"]:
        assert set(event) == {"id", "kind", "at"}

    one = await _call(db, acme, None, "GET", f"/findings/{row.id}")
    assert one.status_code == 200
    body = one.json()
    for member_field in ("assignee_id", "assignee_name", "links"):
        assert member_field not in body
    assert body["case_state"] == "investigating"

    from nexovue_api.data.postgres.models import Project

    elsewhere = Project(org_id=acme.org_id, name="other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(elsewhere)
    await db.flush()
    stranger = FindingRow(
        org_id=acme.org_id,
        project_id=elsewhere.id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        state="open",
        title="x",
        signals=[],
        started_at=NOW,
    )
    db.add(stranger)
    await db.flush()
    refused = await _call(db, acme, None, "GET", f"/findings/{stranger.id}/timeline")
    assert refused.status_code == 404


async def test_a_support_view_reads_the_shape_with_note_bodies_hidden(
    db: AsyncSession, acme: Team
) -> None:
    """AC69's route half (D62); the Reader table test is in test_findings.py's
    sibling `test_reader_kinds_below`."""
    row = await _finding(db, acme, state="open")
    await _call(
        db, acme, acme.member, "POST", f"/findings/{row.id}/notes", {"body": "pasted secret"}
    )

    viewed = await _call(
        db, acme, acme.owner, "GET", f"/findings/{row.id}/timeline", support_view=True
    )

    assert viewed.status_code == 200
    [note_event] = [event for event in viewed.json()["events"] if event["kind"] == "note_added"]
    assert note_event["note_body"] == "Hidden during support view"

    plainly = await _call(db, acme, acme.owner, "GET", f"/findings/{row.id}/timeline")
    [note_event] = [event for event in plainly.json()["events"] if event["kind"] == "note_added"]
    assert note_event["note_body"] == "pasted secret"


def test_only_a_real_session_with_no_ticket_counts_as_a_person(acme: Team) -> None:
    """AC69's table, over every kind of `Reader` that exists today; F4's ticket
    adds its row when it lands."""
    organization = acme.tenant.organization
    person = Reader(
        org_id=organization.id,
        identity=Identity(user=acme.member, organization=organization),
    )
    token = Reader(org_id=organization.id, project_scope=acme.project_id, token_id=uuid.uuid4())
    support = Reader(
        org_id=organization.id,
        identity=Identity(
            user=acme.member,
            organization=organization,
            impersonation=Impersonation(
                impersonation_id="i",
                operator_label="Raj at nexovue",
                reason="r",
                grant_id="g",
                until=datetime(2100, 1, 1, tzinfo=UTC),
            ),
        ),
    )
    assert person.is_real_person
    assert not token.is_real_person
    assert not support.is_real_person


# -------------------------------------------------- clearing an assignee


@pytest.mark.parametrize("route", ["remove", "self_delete", "demote"])
async def test_leaving_or_demotion_clears_every_active_assignment(
    db: AsyncSession, acme: Team, route: str
) -> None:
    """AC10's route half (T1.8): each of the three routes, with its event. The
    sweep half is T2.6's."""
    row = await _finding(db, acme, state="open")
    await _call(
        db, acme, acme.member, "PATCH", f"/findings/{row.id}/case", {"case_state": "investigating"}
    )

    if route == "remove":
        await members.remove(
            db,
            actor=acme.owner,
            organization=acme.tenant.organization,
            member_id=acme.member.id,
            now=NOW,
        )
    elif route == "self_delete":
        await members.scrub(db, member=acme.member, now=NOW)
    else:
        await members.change_role(
            db,
            actor=acme.owner,
            organization=acme.tenant.organization,
            member_id=acme.member.id,
            role="viewer",
            now=NOW,
        )

    row = await _fresh(db, row)
    assert row.assignee_id is None
    assert row.case_state == "investigating"
    [event] = await _events(db, acme, row.id, "unassigned")
    assert event.actor_kind == "system"
    assert event.detail == {"assignee_id": str(acme.member.id)}
