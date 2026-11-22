"""One test per kind of timeline event FR16 names, against real Postgres (F22 plan T8.8;
AC70).

Each test makes the event happen by the code that writes it in production, then shows the
event, **who or what did it**, and **its fields**:

- A person's acts go through the API's routes, and are read back through
  `GET /findings/{id}/timeline` as a member sees it.
- The project job's kinds go through `detection/events.py`, as the job calls it.
- The tracker's kinds go through the real `sync_connection`, with a scripted tracker in
  the seam where GitHub would be (`test_connections_sync_live.py` runs the same rows
  against the simulated GitHub).
- `explained` goes through F3's real `gather`, which writes it beside its notification.

**Why a test per kind and not one loop.** A kind nobody writes passes every test that lists
the vocabulary; this file fails the day a kind has no writer. `explained` was that kind
until this file was written: it was frozen into the vocabulary, and nothing wrote it.

`new_normal` is the one kind not driven through its route: the route needs ClickHouse's
readings (`test_findings_actions_live.py` runs it), so here the route's wiring is read from
its source and the shared operation is run.

The first half runs in one transaction, rolled back afterwards; the second half commits what
it needs and removes its organization.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.connections import (
    CONNECTION_CREDENTIAL_PURPOSE,
    connection_secret_owner,
    issue_facts,
)
from nexovue_platform.detection import FINDING_EVENT_KINDS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from test_findings_cases_live import ASYNC_DSN, NOW, Team, _finding, _reachable, _team
from test_findings_links_live import (
    _attach,
    _call,
    _case_finding,
    _connected,
    _promote,
    _read_once,
)

from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import User
from nexovue_api.findings import cases, service
from nexovue_api.signup.service import provision_tenant
from nexovue_scheduler.alerts.incidents import FLAP_OPENINGS
from nexovue_scheduler.connections import sync
from nexovue_scheduler.connections.trackers import (
    IssueGone,
    IssueMoved,
    IssueRead,
    IssueRef,
    IssueSeen,
    PlaceKey,
    Reach,
    StoredConnection,
)
from nexovue_scheduler.detection import events
from nexovue_scheduler.evidence.gather import gather

require_stack(
    reachable=_reachable(ASYNC_DSN.replace("postgresql+asyncpg://", "postgresql://", 1), 5432),
    suite="apps/api/tests/test_finding_event_kinds_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

# FR16's kinds, which this file holds one test each for (the spec's list, in its order).
FR16_KINDS: Final = (
    "finding_opened",
    "severity_raised",
    "signal_joined",
    "finding_closed",
    "flapping_started",
    "finding_joined_case",
    "finding_left_case",
    "case_started",
    "assigned",
    "unassigned",
    "case_resolved",
    "case_reopened",
    "acknowledged",
    "false_alarm",
    "new_normal",
    "note_added",
    "note_deleted",
    "link_added",
    "link_removed",
    "link_promoted",
    "issue_created",
    "issue_closed",
    "issue_reopened",
    "issue_gone",
    "issue_moved",
    "tracker_write_failed",
    "explained",
)
# F5's two, added to the vocabulary by its own migrations; its suites hold their tests
# (`test_fix_audit_live.py` and the draft's), and this file only names them.
F5_KINDS: Final = ("fix_proposed", "fix_pull_request_opened")

MINUTE: Final = timedelta(minutes=1)
SERVICE: Final = "checkout"


# ------------------------------------------------------------------ the vocabulary


def test_every_kind_in_the_vocabulary_has_a_test_here_or_in_f5() -> None:
    """The list above and the frozen vocabulary agree, and each of FR16's kinds has exactly
    one test whose name begins with it (`test_<kind>_...`)."""
    assert sorted((*FR16_KINDS, *F5_KINDS)) == sorted(FINDING_EVENT_KINDS)
    names = [
        name for name, value in globals().items() if name.startswith("test_") and callable(value)
    ]
    for kind in FR16_KINDS:
        owners = [name for name in names if name.startswith(f"test_{kind}_")]
        assert len(owners) == 1, (kind, owners)


# ----------------------------------------------------------------- part one: in a transaction


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


async def _timeline(db: AsyncSession, team: Team, finding_id: uuid.UUID) -> list[dict[str, Any]]:
    """The page's timeline as a member reads it: the event, its actor and its detail."""
    response = await _call(db, team, team.member, "GET", f"/findings/{finding_id}/timeline")
    assert response.status_code == 200, response.text
    events_read: list[dict[str, Any]] = response.json()["events"]
    return events_read


def _one(timeline: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    found = [event for event in timeline if event["kind"] == kind]
    assert len(found) == 1, (kind, [event["kind"] for event in timeline])
    return found[0]


def _by_person(event: dict[str, Any], person: User, **detail: Any) -> None:
    """A person's act: named, with the person's id, and exactly these fields."""
    assert (event["actor_kind"], event["actor_id"], event["actor_name"]) == (
        "person",
        str(person.id),
        person.name,
    )
    assert event["detail"] == {name: str(value) for name, value in detail.items()}


def _by_system(event: dict[str, Any], **detail: Any) -> None:
    """The system's act: no person, and exactly these fields."""
    assert (event["actor_kind"], event["actor_id"], event["actor_name"]) == ("system", None, None)
    assert event["detail"] == detail


async def _take(
    db: AsyncSession, team: Team, finding: FindingRow, person: User | None = None
) -> None:
    response = await _call(
        db,
        team,
        person or team.member,
        "PATCH",
        f"/findings/{finding.id}/case",
        {"case_state": "investigating"},
    )
    assert response.status_code == 200, response.text


async def _change(db: AsyncSession, team: Team, finding: FindingRow, body: dict[str, Any]) -> None:
    response = await _call(db, team, team.member, "PATCH", f"/findings/{finding.id}/case", body)
    assert response.status_code == 200, response.text


async def _opened_finding(
    db: AsyncSession, team: Team, at: datetime, *, closed: bool = False
) -> FindingRow:
    row = FindingRow(
        org_id=team.org_id,
        project_id=team.project_id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        state="closed" if closed else "open",
        closed_reason="recovered" if closed else None,
        title=f"{SERVICE} is slower than normal",
        signals=[],
        started_at=at,
        closed_at=at + MINUTE if closed else None,
        muted=True,
    )
    db.add(row)
    await db.flush()
    return row


async def _write_opening(db: AsyncSession, team: Team, row: FindingRow, at: datetime) -> None:
    await events.finding_opened(
        await db.connection(),
        org_id=team.org_id,
        project_id=team.project_id,
        finding_id=row.id,
        scope="service",
        service_name=SERVICE,
        severity="warning",
        root_finding_id=None,
        at=at,
    )


# ---- the project job's kinds, by its own writers


async def test_finding_opened_is_written_by_the_job_for_the_system_with_its_severity(
    db: AsyncSession, acme: Team
) -> None:
    row = await _opened_finding(db, acme, NOW)

    await _write_opening(db, acme, row, NOW)

    _by_system(_one(await _timeline(db, acme, row.id), "finding_opened"), severity="warning")


async def test_severity_raised_names_the_new_severity_and_the_system(
    db: AsyncSession, acme: Team
) -> None:
    row = await _opened_finding(db, acme, NOW)
    await _write_opening(db, acme, row, NOW)

    await events.severity_raised(
        await db.connection(), org_id=acme.org_id, finding_id=row.id, at=NOW + MINUTE
    )

    _by_system(_one(await _timeline(db, acme, row.id), "severity_raised"), severity="critical")


async def test_signal_joined_counts_the_signals_and_names_the_system(
    db: AsyncSession, acme: Team
) -> None:
    row = await _opened_finding(db, acme, NOW)
    await _write_opening(db, acme, row, NOW)

    await events.signal_joined(
        await db.connection(),
        org_id=acme.org_id,
        finding_id=row.id,
        signal_count=2,
        at=NOW + MINUTE,
    )

    _by_system(_one(await _timeline(db, acme, row.id), "signal_joined"), signal_count=2)


async def test_finding_closed_says_how_it_closed_and_names_the_system(
    db: AsyncSession, acme: Team
) -> None:
    row = await _opened_finding(db, acme, NOW)
    await _write_opening(db, acme, row, NOW)

    await events.finding_closed(
        await db.connection(),
        org_id=acme.org_id,
        finding_id=row.id,
        reason="recovered",
        at=NOW + MINUTE,
    )

    _by_system(_one(await _timeline(db, acme, row.id), "finding_closed"), closed_reason="recovered")


async def test_flapping_started_is_one_event_for_the_spell_and_names_the_system(
    db: AsyncSession, acme: Team
) -> None:
    last: FindingRow | None = None
    for index in range(FLAP_OPENINGS):
        at = NOW + index * 2 * MINUTE
        last = await _opened_finding(db, acme, at, closed=index < FLAP_OPENINGS - 1)
        await _write_opening(db, acme, last, at)
    assert last is not None

    _by_system(_one(await _timeline(db, acme, last.id), "flapping_started"))


# ---- a person's acts, through the routes


async def test_case_started_names_the_person_who_took_it(db: AsyncSession, acme: Team) -> None:
    finding = await _case_finding(db, acme)

    await _take(db, acme, finding)

    _by_person(_one(await _timeline(db, acme, finding.id), "case_started"), acme.member)


async def test_acknowledged_is_written_when_taking_an_open_finding_for_the_person(
    db: AsyncSession, acme: Team
) -> None:
    finding = await _case_finding(db, acme)

    await _take(db, acme, finding)

    _by_person(_one(await _timeline(db, acme, finding.id), "acknowledged"), acme.member)


async def test_assigned_names_the_person_acting_and_the_new_assignee(
    db: AsyncSession, acme: Team
) -> None:
    finding = await _case_finding(db, acme)
    await _take(db, acme, finding)

    await _change(db, acme, finding, {"assignee_id": str(acme.second_member.id)})

    assigned = [e for e in await _timeline(db, acme, finding.id) if e["kind"] == "assigned"]
    # Newest first: the handover, then the take's own.
    assert len(assigned) == 2
    _by_person(assigned[0], acme.member, assignee_id=acme.second_member.id)
    _by_person(assigned[1], acme.member, assignee_id=acme.member.id)


async def test_unassigned_names_the_person_acting_and_who_was_unassigned(
    db: AsyncSession, acme: Team
) -> None:
    finding = await _case_finding(db, acme)
    await _take(db, acme, finding)

    await _change(db, acme, finding, {"assignee_id": None})

    _by_person(
        _one(await _timeline(db, acme, finding.id), "unassigned"),
        acme.member,
        assignee_id=acme.member.id,
    )


async def test_case_resolved_says_how_it_was_resolved(db: AsyncSession, acme: Team) -> None:
    finding = await _case_finding(db, acme)
    await _take(db, acme, finding)

    await _change(db, acme, finding, {"case_state": "resolved"})

    _by_person(
        _one(await _timeline(db, acme, finding.id), "case_resolved"),
        acme.member,
        resolution="resolved",
    )


async def test_case_reopened_names_the_person_who_reopened_it(db: AsyncSession, acme: Team) -> None:
    finding = await _case_finding(db, acme)
    await _take(db, acme, finding)
    await _change(db, acme, finding, {"case_state": "resolved"})

    await _change(db, acme, finding, {"case_state": "investigating"})

    _by_person(_one(await _timeline(db, acme, finding.id), "case_reopened"), acme.member)


async def test_false_alarm_names_the_person_who_said_so(db: AsyncSession, acme: Team) -> None:
    finding = await _case_finding(db, acme)

    response = await _call(
        db, acme, acme.member, "POST", f"/findings/{finding.id}/false-alarm", {"reason": None}
    )

    assert response.status_code == 200, response.text
    _by_person(_one(await _timeline(db, acme, finding.id), "false_alarm"), acme.member)


async def test_new_normal_names_the_person_who_set_it_and_the_route_runs_that_operation(
    db: AsyncSession, acme: Team
) -> None:
    """The operation the route runs after its own work, here with the finding's case taken so
    that it also resolves the case with this reason. The route itself is
    `test_findings_actions_live.py`'s, which needs ClickHouse readings; the wiring is read."""
    wiring = inspect.getsource(service.accept_new_normal)
    assert 'cases.on_never_a_problem(session, row, root, actor, kind="new_normal"' in wiring
    finding = await _case_finding(db, acme)
    await _take(db, acme, finding)
    await db.refresh(finding)

    await cases.on_never_a_problem(
        db, finding, None, acme.admin, kind="new_normal", now=NOW + MINUTE
    )
    await db.commit()

    timeline = await _timeline(db, acme, finding.id)
    _by_person(_one(timeline, "new_normal"), acme.admin)
    _by_person(_one(timeline, "case_resolved"), acme.admin, resolution="new_normal")


async def test_note_added_names_the_author_and_the_note_and_shows_its_words(
    db: AsyncSession, acme: Team
) -> None:
    finding = await _case_finding(db, acme)

    written = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/findings/{finding.id}/notes",
        {"body": "Restarted the pool."},
    )

    assert written.status_code == 200, written.text
    event = _one(await _timeline(db, acme, finding.id), "note_added")
    _by_person(event, acme.member, note_id=written.json()["note"]["id"])
    assert event["note_body"] == "Restarted the pool."


async def test_note_deleted_names_who_deleted_it_and_the_words_are_gone(
    db: AsyncSession, acme: Team
) -> None:
    finding = await _case_finding(db, acme)
    written = await _call(
        db,
        acme,
        acme.member,
        "POST",
        f"/findings/{finding.id}/notes",
        {"body": "A secret by mistake."},
    )
    note_id = written.json()["note"]["id"]

    deleted = await _call(db, acme, acme.admin, "DELETE", f"/findings/{finding.id}/notes/{note_id}")

    assert deleted.status_code == 200, deleted.text
    timeline = await _timeline(db, acme, finding.id)
    _by_person(_one(timeline, "note_deleted"), acme.admin, note_id=note_id)
    assert _one(timeline, "note_added")["note_body"] is None


async def test_link_added_names_the_person_and_the_link(db: AsyncSession, acme: Team) -> None:
    await _connected(db, acme)
    finding = await _case_finding(db, acme)

    attached = await _attach(db, acme, finding)

    assert attached.status_code == 201, attached.text
    _by_person(
        _one(await _timeline(db, acme, finding.id), "link_added"),
        acme.member,
        link_id=attached.json()["link_id"],
    )


async def test_link_removed_names_the_person_and_the_link(db: AsyncSession, acme: Team) -> None:
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = (await _attach(db, acme, finding)).json()["link_id"]

    removed = await _call(
        db, acme, acme.member, "DELETE", f"/findings/{finding.id}/links/{link_id}"
    )

    assert removed.status_code == 200, removed.text
    _by_person(
        _one(await _timeline(db, acme, finding.id), "link_removed"), acme.member, link_id=link_id
    )


async def test_link_promoted_names_the_person_and_the_link(db: AsyncSession, acme: Team) -> None:
    await _connected(db, acme)
    finding = await _case_finding(db, acme)
    link_id = (await _attach(db, acme, finding)).json()["link_id"]
    await _read_once(db, uuid.UUID(link_id))

    promoted = await _promote(db, acme, finding, uuid.UUID(link_id))

    assert promoted.status_code == 202, promoted.text
    _by_person(
        _one(await _timeline(db, acme, finding.id), "link_promoted"), acme.member, link_id=link_id
    )


async def test_finding_joined_case_names_the_person_and_the_finding_that_joined(
    db: AsyncSession, acme: Team
) -> None:
    """Starting a case on a closed finding brings in the service's open one (FR11)."""
    closed = await _finding(db, acme, state="closed")
    still_open = await _finding(db, acme)

    await _take(db, acme, closed)

    _by_person(
        _one(await _timeline(db, acme, closed.id), "finding_joined_case"),
        acme.member,
        joined_finding_id=still_open.id,
    )


async def test_finding_left_case_names_the_person_resolving_and_the_finding_that_left(
    db: AsyncSession, acme: Team
) -> None:
    closed = await _finding(db, acme, state="closed")
    still_open = await _finding(db, acme)
    await _take(db, acme, closed)

    await _change(db, acme, closed, {"case_state": "resolved"})

    _by_person(
        _one(await _timeline(db, acme, closed.id), "finding_left_case"),
        acme.member,
        left_finding_id=still_open.id,
    )


# ----------------------------------------------------------------- part two: committed

KEY: Final = generate_key()
KEYS: Final = ConfiguredKeys(KEY)
BASE_URL: Final = "https://nexovue.acme.example"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@dataclass
class World:
    org_id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    place_id: uuid.UUID
    engine: AsyncEngine
    now: datetime


@pytest.fixture
async def world(engine: AsyncEngine) -> AsyncIterator[World]:
    """An organization with a working GitHub connection and one repository, committed, and
    removed afterwards."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        tenant = await provision_tenant(
            session,
            organization_name="Event kinds",
            email=f"owner-{uuid.uuid4()}@example.test",
            password="correct horse battery staple",
        )
        await session.commit()
    org_id, project_id = tenant.organization.id, tenant.project.id
    connection_id = uuid.uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    sealed = SealedBox(
        keyring_from(KEYS.current, KEYS.previous), purpose=CONNECTION_CREDENTIAL_PURPOSE
    ).seal("a scripted credential", owner=connection_secret_owner(str(org_id), str(connection_id)))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state, sealed_credential, "
                "github_app_id, github_installation_id, repositories_read_at, last_ok_at) "
                "VALUES (:id, :org, 'github', 'working', :key, :app, :installation, :now, :now)"
            ),
            {
                "id": connection_id,
                "org": org_id,
                "key": sealed,
                "app": 2_000_000 + uuid.uuid4().int % 10**6,
                "installation": 3_000_000 + uuid.uuid4().int % 10**6,
                "now": now,
            },
        )
        place_id = (
            await connection.execute(
                text(
                    "INSERT INTO connection_repositories (org_id, connection_id, external_id, "
                    "key, is_private) VALUES (:org, :connection, '4000001', 'acme/api', true) "
                    "RETURNING id"
                ),
                {"org": org_id, "connection": connection_id},
            )
        ).scalar_one()
    yield World(org_id, project_id, connection_id, place_id, engine, now)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})


async def _case(world: World, *, signal: str = "open") -> uuid.UUID:
    """A finding with an active case, its signal open or recovered."""
    async with world.engine.begin() as connection:
        found: uuid.UUID = (
            await connection.execute(
                text(
                    """
                    INSERT INTO findings (org_id, project_id, scope, service_name, severity,
                        title, started_at, state, closed_at, closed_reason, signals, case_state,
                        case_opened_at, case_activity_at)
                    VALUES (:org, :project, 'service', :service, 'critical', 'x', :started,
                        :state, :closed_at, :closed_reason, CAST(:signals AS jsonb),
                        'investigating', :opened, :opened)
                    RETURNING id
                    """
                ),
                {
                    "org": world.org_id,
                    "project": world.project_id,
                    "service": SERVICE,
                    "started": world.now - timedelta(days=1),
                    "state": signal,
                    "closed_at": None if signal == "open" else world.now - timedelta(hours=2),
                    "closed_reason": None if signal == "open" else "recovered",
                    "signals": json.dumps(
                        [{"signal": "slower", "current": 812.0, "normal": 203.0, "comparison": "x"}]
                    ),
                    "opened": world.now - timedelta(hours=3),
                },
            )
        ).scalar_one()
    return found


async def _link(
    world: World,
    finding_id: uuid.UUID,
    *,
    creating: bool = False,
    seen: str = "open",
    held: bool = False,
) -> uuid.UUID:
    """A link due now: waiting to be created, or a synced issue as last seen."""
    facts = issue_facts(
        finding_id=finding_id,
        signal="slower",
        current=812.0,
        normal=203.0,
        severity="critical",
        since=world.now - timedelta(days=1),
        service_name=SERVICE,
        base_url=BASE_URL,
    )
    issue = uuid.uuid4().int % 10**7
    async with world.engine.begin() as connection:
        made: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO finding_links (org_id, finding_id, connection_id, place_id, "
                    "role, sync_status, external_id, external_key, seen_state, frozen_facts, "
                    "tracker_closed_at, next_sync_at) VALUES (:org, :finding, :connection, "
                    ":place, 'synced', :status, :external_id, :key, :seen, "
                    "CAST(:facts AS jsonb), :held, :now) RETURNING id"
                ),
                {
                    "org": world.org_id,
                    "finding": finding_id,
                    "connection": world.connection_id,
                    "place": world.place_id,
                    "status": "creating" if creating else "in_sync",
                    "external_id": None if creating else str(issue),
                    "key": None if creating else f"acme/api#{issue}",
                    "seen": None if creating else seen,
                    "facts": json.dumps(facts.to_json()) if creating else None,
                    "held": world.now - timedelta(minutes=10) if held else None,
                    "now": world.now,
                },
            )
        ).scalar_one()
    return made


class Scripted:
    """What a tracker answers, by script: the seam's other side, with no network."""

    search_settles_seconds = 0.0

    def __init__(self, *, read: IssueRead | None = None, public: bool = False) -> None:
        self._read = read
        self._public = public

    def check(self) -> None:
        return None

    def reach(self) -> Reach:
        return Reach(places=())

    def read(self, place: PlaceKey, issue: IssueRef) -> IssueRead:
        assert self._read is not None
        return self._read

    def find_existing(self, place: PlaceKey, link_back: str, since: datetime) -> IssueRef | None:
        return None

    def create(self, place: PlaceKey, text: Any) -> IssueRef:
        return IssueRef("9000001", "acme/api#1")

    def set_status(self, place: PlaceKey, issue: IssueRef, wanted: str, close_reason: str) -> None:
        return None

    def comment(self, place: PlaceKey, issue: IssueRef, text: Any) -> None:
        return None

    def is_public(self, place: PlaceKey) -> bool:
        return self._public

    def finish(self) -> None:
        return None


async def _run_sync(world: World, tracker: Scripted) -> None:
    async def no_wait(seconds: float) -> None:
        return None

    def scripted(stored: StoredConnection) -> Scripted:
        return tracker

    await sync.sync_connection(
        world.engine,
        org_id=str(world.org_id),
        connection_id=str(world.connection_id),
        keys=KEYS,
        tracker_of=scripted,
        clock=lambda: world.now,
        pause=no_wait,
    )


async def _written(world: World, finding_id: uuid.UUID, kind: str) -> Any:
    """The one event of this kind on the finding, as the timeline reads it from the table."""
    async with world.engine.connect() as connection:
        rows = list(
            await connection.execute(
                text(
                    "SELECT kind, actor_kind, actor_id, detail FROM finding_events "
                    "WHERE org_id = :org AND finding_id = :id AND kind = :kind"
                ),
                {"org": world.org_id, "id": finding_id, "kind": kind},
            )
        )
    assert len(rows) == 1, (kind, rows)
    return rows[0]


def _by_tracker_side(row: Any, actor: str, **detail: Any) -> None:
    assert (row.actor_kind, row.actor_id) == (actor, None)
    assert row.detail == detail


async def test_issue_created_is_written_for_the_system_with_the_link(world: World) -> None:
    finding = await _case(world)
    link = await _link(world, finding, creating=True)

    await _run_sync(world, Scripted())

    _by_tracker_side(await _written(world, finding, "issue_created"), "system", link_id=str(link))


async def test_tracker_write_failed_names_the_failure_as_a_word_and_the_link(world: World) -> None:
    finding = await _case(world)
    link = await _link(world, finding, creating=True)

    await _run_sync(world, Scripted(public=True))

    _by_tracker_side(
        await _written(world, finding, "tracker_write_failed"),
        "system",
        link_id=str(link),
        failure="public_repository",
    )


async def test_issue_closed_names_the_tracker_and_how_it_was_closed(world: World) -> None:
    finding = await _case(world, signal="open")
    link = await _link(world, finding)

    await _run_sync(world, Scripted(read=IssueSeen("closed", "not_planned")))

    _by_tracker_side(
        await _written(world, finding, "issue_closed"),
        "tracker",
        link_id=str(link),
        close_reason="not_planned",
    )


async def test_issue_reopened_names_the_tracker_and_the_link(world: World) -> None:
    finding = await _case(world, signal="open")
    link = await _link(world, finding, seen="closed", held=True)

    await _run_sync(world, Scripted(read=IssueSeen("open", None)))

    _by_tracker_side(await _written(world, finding, "issue_reopened"), "tracker", link_id=str(link))


async def test_issue_gone_names_the_tracker_and_the_link(world: World) -> None:
    finding = await _case(world)
    link = await _link(world, finding)

    await _run_sync(world, Scripted(read=IssueGone()))

    _by_tracker_side(await _written(world, finding, "issue_gone"), "tracker", link_id=str(link))


async def test_issue_moved_names_the_tracker_and_the_link(world: World) -> None:
    finding = await _case(world)
    link = await _link(world, finding)

    await _run_sync(world, Scripted(read=IssueMoved("elsewhere/api", 3, "9000003")))

    _by_tracker_side(await _written(world, finding, "issue_moved"), "tracker", link_id=str(link))


# ---- explained, F3's follow-up, through its own gather

SETTLED_AFTER: Final = timedelta(minutes=6)


async def _told_finding(world: World, started: datetime) -> uuid.UUID:
    """An open finding told to a channel people read, with a signed deploy just before it:
    the one cause that is `likely` and verified, so the settled read owes a follow-up. Due a
    month from now, so the stack's own delivery sweep never takes the notification."""
    async with world.engine.begin() as connection:
        finding_id: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO findings (org_id, project_id, scope, service_name, severity, "
                    "state, title, signals, started_at) VALUES (:org, :project, 'service', "
                    ":service, 'warning', 'open', 'x', '[]', :started) RETURNING id"
                ),
                {
                    "org": world.org_id,
                    "project": world.project_id,
                    "service": SERVICE,
                    "started": started,
                },
            )
        ).scalar_one()
        channel_id = (
            await connection.execute(
                text(
                    "INSERT INTO notification_channels (org_id, kind, name, config) "
                    "VALUES (:org, 'slack', 'channel', '{}') RETURNING id"
                ),
                {"org": world.org_id},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO notifications (org_id, project_id, finding_id, channel_id, kind, "
                "sequence, severity, deliver_after, created_at) VALUES (:org, :project, :finding, "
                ":channel, 'opened', 0, 'warning', :at, :at)"
            ),
            {
                "org": world.org_id,
                "project": world.project_id,
                "finding": finding_id,
                "channel": channel_id,
                "at": started,
            },
        )
        deployed = started - timedelta(minutes=4)
        release_id = await connection.scalar(
            text(
                "INSERT INTO releases (org_id, project_id, service_name, version, "
                "first_reported_at, last_reported_at, report_count, last_state, "
                "last_activity_at) VALUES (:org, :project, :service, 'v2.1', :first, :at, 1, "
                "'finished', :at) RETURNING id"
            ),
            {
                "org": world.org_id,
                "project": world.project_id,
                "service": SERVICE,
                "first": deployed - timedelta(days=21),
                "at": deployed,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO deploy_events (org_id, project_id, version, service_name, "
                "deployed_at, source, delivery_id, metadata, state, release_id) VALUES (:org, "
                ":project, 'v2.1', :service, :at, 'ci', :delivery, '{}', 'finished', :release)"
            ),
            {
                "org": world.org_id,
                "project": world.project_id,
                "service": SERVICE,
                "at": deployed,
                "delivery": str(uuid.uuid4()),
                "release": release_id,
            },
        )
    return finding_id


async def _read(world: World, finding_id: uuid.UUID, reason: str, now: datetime) -> None:
    await gather(
        world.engine,
        None,
        finding_id=finding_id,
        org_id=world.org_id,
        reason=reason,  # type: ignore[arg-type]
        deployment="self-hosted",
        now=now,
        show_held_causes=True,
    )


async def _explained_events(world: World, finding_id: uuid.UUID) -> list[Any]:
    async with world.engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT actor_kind, actor_id, detail FROM finding_events "
                    "WHERE org_id = :org AND finding_id = :id AND kind = 'explained'"
                ),
                {"org": world.org_id, "id": finding_id},
            )
        )


async def test_explained_is_written_for_the_system_with_the_version_it_followed_up(
    world: World,
) -> None:
    started = world.now + timedelta(days=30)
    finding = await _told_finding(world, started)

    await _read(world, finding, "settled", started + SETTLED_AFTER)

    written_events = await _explained_events(world, finding)
    assert len(written_events) == 1, "the follow-up was owed and no explained event was written"
    [written] = written_events
    assert (written.actor_kind, written.actor_id) == ("system", None)
    assert written.detail == {"explanation_version": 1}


async def test_a_read_that_sends_no_follow_up_leaves_no_explained_event(world: World) -> None:
    """The event records a follow-up that was owed and written; the opening's read, which
    sends none, leaves the timeline as it was."""
    started = world.now + timedelta(days=30)
    finding = await _told_finding(world, started)

    await _read(world, finding, "opened", started + timedelta(seconds=30))

    assert await _explained_events(world, finding) == []
