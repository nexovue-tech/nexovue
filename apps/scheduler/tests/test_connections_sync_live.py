"""Creating an issue and staying in step by polling, through the real job, against real
Postgres and the simulated GitHub (F22 plan T4.5; design, *The job*, *The comparison*,
*Creating without duplicates*).

**Against a fake, by the founder's decision of 2026-11-08** (foundation item 28):
`tracker_fake.py` answers as GitHub's documentation reads; T4.8's run against real GitHub
is not yet run, and the founder runs it after Phase 2.

What is proved here, each through `sync_connection` itself:

- the issue is the preview's bytes, from the facts frozen at the click (AC83), labelled,
  written with a token narrowed to its one repository and revoked after the run;
- the public check before every create, with a withdrawn or never given allowance, a
  repository that turned public between tries, and a check that failed (AC51);
- every row of the comparison, and running twice changing nothing (AC45);
- a close and its comment in that order, the comment at most once (AC87), and the close
  reason following how the case ended;
- a worker killed at each named fault point leaving exactly one issue (AC44), on a
  tracker whose list sees a write only after a stated delay;
- the run ending at the first failure that may pass, and a rate limit pausing the
  connection for as long as it asked (AC49, AC86); writes a second apart;
- a disconnect between two links stopping the next write (AC37);
- 410, 404 and 301, and a repository leaving and returning to the installation (AC52);
- a recovery leaving the link alone (AC4), a resolve made while sync was stopped
  reaching the tracker later (AC55), a replaced key resuming the same issue (AC75), and
  a `no_activity` case reopened there staying 90 days with no second comment (AC96);
- a job naming another organization's connection doing nothing (AC129), and a held
  lease stopping a second run;
- `sweep_connections` adding one job per connection however often it runs (AC48's
  conditional insert), and each connection's hourly refresh.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import ASYNC_DSN, AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.cases import CASE_IDLE_LIMIT
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.connections import (
    ACTIVE_POLL,
    CONNECTION_CREDENTIAL_PURPOSE,
    REFRESH_CONNECTION_TASK,
    RESOLVED_POLL,
    SYNC_CONNECTION_TASK,
    comment_text,
    connection_secret_owner,
    issue_facts,
    issue_text,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github
from tracker_harness import (
    COMMENT_POINTS,
    CREATION_POINTS,
    KILLED,
    TRACKER_POINT,
    kill_after_commit,
    run_child,
)

from nexovue_scheduler.connections import sync, tasks
from nexovue_scheduler.connections.github import GitHubEndpoint, GitHubTracker
from nexovue_scheduler.connections.trackers import StoredConnection, Tracker

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_connections_sync_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

KEY: Final = generate_key()
KEYS: Final = ConfiguredKeys(KEY)
BASE_URL: Final = "https://nexovue.acme.example"
API_ID: Final = 4000001
WEB_ID: Final = 4000002
OUTSIDE_ID: Final = 4000003


class Clock:
    """One clock for the job and the simulated GitHub's list, moved by the test."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def seconds(self) -> float:
        return self.now.timestamp()

    def advance(self, by: timedelta) -> None:
        self.now += by


@dataclass
class World:
    tenant: AlertTenant
    github: FakeGitHub
    clock: Clock
    connection_id: uuid.UUID
    api_place: uuid.UUID
    web_place: uuid.UUID
    pauses: list[float] = field(default_factory=list)
    settles: float = 0.0

    @property
    def ids(self) -> dict[str, str]:
        return {"org_id": str(self.tenant.org_id), "connection_id": str(self.connection_id)}


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with serve_fake_github() as fake:
        yield fake


@pytest.fixture
async def world(engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub) -> World:
    clock = Clock()
    github.clock = clock.seconds
    # An App and installation of this test's own: the pair is unique across every
    # tenant, and other suites use the fake's defaults at the same time.
    github.app_id = 2_000_000 + uuid.uuid4().int % 10**6
    installation = github.install(3_000_000 + uuid.uuid4().int % 10**6)
    github.add_repository(API_ID, "acme/api")
    github.add_repository(WEB_ID, "acme/web")
    github.add_repository(OUTSIDE_ID, "elsewhere/api")
    connection_id = uuid.uuid4()
    sealed = _box().seal(
        github.private_key_pem,
        owner=connection_secret_owner(str(tenant.org_id), str(connection_id)),
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state, sealed_credential, "
                "github_app_id, github_app_slug, github_installation_id, repositories_read_at, "
                "last_ok_at) VALUES (:id, :org, 'github', 'working', :key, :app, :slug, "
                ":installation, :now, :now)"
            ),
            {
                "id": connection_id,
                "org": tenant.org_id,
                "key": sealed,
                "app": github.app_id,
                "slug": github.app_slug,
                "installation": installation,
                "now": clock.now,
            },
        )
        places = {}
        for external_id, key in ((API_ID, "acme/api"), (WEB_ID, "acme/web")):
            places[key] = (
                await connection.execute(
                    text(
                        "INSERT INTO connection_repositories (org_id, connection_id, external_id, "
                        "key, is_private) VALUES (:org, :connection, :external_id, :key, true) "
                        "RETURNING id"
                    ),
                    {
                        "org": tenant.org_id,
                        "connection": connection_id,
                        "external_id": str(external_id),
                        "key": key,
                    },
                )
            ).scalar_one()
    return World(tenant, github, clock, connection_id, places["acme/api"], places["acme/web"])


def _box() -> SealedBox:
    return SealedBox(
        keyring_from(KEYS.current, KEYS.previous), purpose=CONNECTION_CREDENTIAL_PURPOSE
    )


def _tracker_of(world: World) -> Any:
    def build(stored: StoredConnection) -> Tracker:
        return GitHubTracker.for_connection(
            stored,
            GitHubEndpoint(base_url=world.github.base_url, private_allowed=True),
            search_settles_seconds=world.settles,
        )

    return build


async def _sync(engine: AsyncEngine, world: World, **ids: str) -> list[str]:
    async def pause(seconds: float) -> None:
        world.pauses.append(seconds)

    return await sync.sync_connection(
        engine,
        keys=KEYS,
        tracker_of=_tracker_of(world),
        clock=world.clock,
        pause=pause,
        **(ids or world.ids),
    )


# ------------------------------------------------------------------ arranging


async def _case(
    engine: AsyncEngine,
    world: World,
    *,
    service: str = "checkout",
    signal: str = "closed",
    case_state: str | None = "investigating",
    resolved_ago: timedelta = timedelta(hours=1),
    activity_ago: timedelta = timedelta(hours=1),
    resolution: str = "resolved",
) -> uuid.UUID:
    now = world.clock.now
    resolved = case_state == "resolved"
    async with engine.begin() as connection:
        finding_id: uuid.UUID = (
            await connection.execute(
                text(
                    """
                    INSERT INTO findings (org_id, project_id, scope, service_name, severity, title,
                        started_at, state, closed_at, closed_reason, signals, case_state,
                        case_opened_at, case_activity_at, case_resolved_at, case_resolution)
                    VALUES (:org, :project, 'service', :service, 'critical', 'x', :started,
                        :state, :closed_at, :closed_reason, CAST(:signals AS jsonb), :case_state,
                        :opened, :activity, :resolved_at, :resolution)
                    RETURNING id
                    """
                ),
                {
                    "org": world.tenant.org_id,
                    "project": world.tenant.project_id,
                    "service": service,
                    "started": now - timedelta(days=1),
                    "state": signal,
                    "closed_at": None if signal == "open" else now - timedelta(hours=2),
                    "closed_reason": None if signal == "open" else "recovered",
                    "signals": json.dumps(
                        [{"signal": "slower", "current": 812.0, "normal": 203.0, "comparison": "x"}]
                    ),
                    "case_state": case_state,
                    "opened": None if case_state is None else now - timedelta(days=1),
                    "activity": None if case_state is None else now - activity_ago,
                    "resolved_at": now - resolved_ago if resolved else None,
                    "resolution": resolution if resolved else None,
                },
            )
        ).scalar_one()
    return finding_id


def _facts(world: World, finding_id: uuid.UUID, severity: str = "critical") -> Any:
    return issue_facts(
        finding_id=finding_id,
        signal="slower",
        current=812.0,
        normal=203.0,
        severity=severity,
        since=world.clock.now - timedelta(days=1),
        service_name="checkout",
        base_url=BASE_URL,
    )


async def _creating(
    engine: AsyncEngine, world: World, finding_id: uuid.UUID, *, place: uuid.UUID | None = None
) -> uuid.UUID:
    async with engine.begin() as connection:
        link_id: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO finding_links (org_id, finding_id, connection_id, place_id, role, "
                    "sync_status, frozen_facts, next_sync_at) VALUES (:org, :finding, :connection, "
                    ":place, 'synced', 'creating', CAST(:facts AS jsonb), :now) RETURNING id"
                ),
                {
                    "org": world.tenant.org_id,
                    "finding": finding_id,
                    "connection": world.connection_id,
                    "place": place or world.api_place,
                    "facts": json.dumps(_facts(world, finding_id).to_json()),
                    "now": world.clock.now,
                },
            )
        ).scalar_one()
    return link_id


async def _synced(
    engine: AsyncEngine,
    world: World,
    finding_id: uuid.UUID,
    *,
    seen: str = "open",
    wanted: str | None = None,
    comment: str | None = None,
    held: bool = False,
    issue_state: str | None = None,
    repository: str = "acme/api",
) -> tuple[uuid.UUID, str]:
    """A synced link to a real issue in the simulated GitHub, due now."""
    issue = world.github._new_issue(
        world.github.repository(repository)["id"], "t", f"x: {BASE_URL}/findings/{finding_id}\n", []
    )
    issue.state = issue_state or seen
    if issue.state == "closed":
        issue.state_reason = "completed"
    key = f"{repository}#{issue.number}"
    place = world.api_place if repository == "acme/api" else world.web_place
    async with engine.begin() as connection:
        link_id: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO finding_links (org_id, finding_id, connection_id, place_id, role, "
                    "sync_status, external_id, external_key, seen_state, wanted_state, "
                    "comment_wanted, tracker_closed_at, next_sync_at) VALUES (:org, :finding, "
                    ":connection, :place, 'synced', 'in_sync', :external_id, :key, :seen, :wanted, "
                    ":comment, :held, :now) RETURNING id"
                ),
                {
                    "org": world.tenant.org_id,
                    "finding": finding_id,
                    "connection": world.connection_id,
                    "place": place,
                    "external_id": str(issue.id),
                    "key": key,
                    "seen": seen,
                    "wanted": wanted,
                    "comment": comment,
                    "held": world.clock.now - timedelta(minutes=10) if held else None,
                    "now": world.clock.now,
                },
            )
        ).scalar_one()
    return link_id, key


async def _one(engine: AsyncEngine, sql: str, **values: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), values)).one()


async def _link(engine: AsyncEngine, link_id: uuid.UUID) -> Any:
    return await _one(engine, "SELECT * FROM finding_links WHERE id = :id", id=link_id)


async def _finding(engine: AsyncEngine, finding_id: uuid.UUID) -> Any:
    return await _one(engine, "SELECT * FROM findings WHERE id = :id", id=finding_id)


async def _events(
    engine: AsyncEngine, finding_id: uuid.UUID
) -> list[tuple[str, str, dict[str, Any]]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT kind, actor_kind, detail FROM finding_events WHERE finding_id = :id "
                "ORDER BY at, id"
            ),
            {"id": finding_id},
        )
    return [(row.kind, row.actor_kind, row.detail) for row in rows]


async def _emails(engine: AsyncEngine, finding_id: uuid.UUID) -> list[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT kind FROM finding_emails_owed WHERE finding_id = :id ORDER BY at, id"),
            {"id": finding_id},
        )
    return [row.kind for row in rows]


def _writes(github: FakeGitHub) -> list[tuple[str, str]]:
    return [
        (request.method, request.path)
        for request in github.site.requests
        if request.method in ("POST", "PATCH") and request.path.startswith("/repos/")
    ]


async def _connection(engine: AsyncEngine, world: World) -> Any:
    return await _one(engine, "SELECT * FROM connections WHERE id = :id", id=world.connection_id)


# --------------------------------------------------------- creating (AC83, AC51)


async def test_the_issue_is_the_preview_s_bytes_written_by_a_narrowed_token(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id = await _creating(engine, world, finding)
    preview = issue_text(_facts(world, finding))

    # The severity changes after the click: the issue still says what the preview said.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE findings SET severity = 'warning' WHERE id = :id"), {"id": finding}
        )
    assert await _sync(engine, world) == [str(link_id)]

    [issue] = world.github.issues_in("acme/api")
    assert (issue.title, issue.body, issue.labels) == (preview.title, preview.markdown, ["nexovue"])
    link = await _link(engine, link_id)
    assert (link.sync_status, link.seen_state, link.frozen_facts) == ("in_sync", "open", None)
    assert link.external_key == f"acme/api#{issue.number}" and link.external_id == str(issue.id)
    assert link.next_sync_at == world.clock.now + ACTIVE_POLL
    assert ("issue_created", "system", {"link_id": str(link_id)}) in await _events(engine, finding)
    # One token for the run, for that one repository, revoked when the run ended.
    [token] = world.github.tokens.values()
    assert token.repository_ids == [API_ID]
    assert token.permissions == {"issues": "write", "metadata": "read"}
    assert token.revoked


async def test_a_public_repository_is_refused_at_creation(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id = await _creating(engine, world, finding)
    world.github.make_public("acme/api")  # private when the page was opened

    await _sync(engine, world)

    assert world.github.issues_in("acme/api") == []
    assert not [call for call in _writes(world.github) if call[1].endswith("/issues")]
    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure, link.frozen_facts) == (
        "refused",
        "public_repository",
        None,
    )
    assert (
        "tracker_write_failed",
        "system",
        {"link_id": str(link_id), "failure": "public_repository"},
    ) in (await _events(engine, finding))


async def test_an_allowed_public_repository_creates_and_a_withdrawn_one_refuses(
    engine: AsyncEngine, world: World
) -> None:
    world.github.make_public("acme/api")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connection_repositories SET public_allowed_at = :now WHERE id = :id"),
            {"now": world.clock.now, "id": world.api_place},
        )
    allowed = await _creating(engine, world, await _case(engine, world))
    await _sync(engine, world)
    assert (await _link(engine, allowed)).sync_status == "in_sync"

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connection_repositories SET public_allowed_at = NULL WHERE id = :id"),
            {"id": world.api_place},
        )
    withdrawn = await _creating(engine, world, await _case(engine, world, service="payments"))
    await _sync(engine, world)
    assert (await _link(engine, withdrawn)).sync_status == "refused"
    assert len(world.github.issues_in("acme/api")) == 1


async def test_a_repository_turned_public_between_tries_is_refused_on_the_retry(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id = await _creating(engine, world, finding)
    world.github.fail("create_issue", 502)
    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure) == ("creating", "server_error")
    assert link.first_attempt_at is not None

    world.github.heal("create_issue")
    world.github.make_public("acme/api")
    world.clock.advance(timedelta(hours=2))
    await _sync(engine, world)

    assert (await _link(engine, link_id)).sync_status == "refused"
    assert world.github.issues_in("acme/api") == []


async def test_a_public_check_that_fails_creates_nothing(engine: AsyncEngine, world: World) -> None:
    link_id = await _creating(engine, world, await _case(engine, world))
    world.github.fail("repository", 503)

    await _sync(engine, world)

    assert world.github.issues_in("acme/api") == []
    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure, link.failures) == ("creating", "server_error", 1)
    assert (await _connection(engine, world)).paused_until > world.clock.now


# --------------------------------------------------- killed at a fault point (AC44)


@pytest.mark.parametrize("point", [*CREATION_POINTS, TRACKER_POINT])
async def test_a_worker_killed_while_creating_leaves_exactly_one_issue(
    engine: AsyncEngine, world: World, point: str
) -> None:
    """On a tracker whose list sees a new issue only after 60 seconds, a retry that
    looks only once 120 seconds have passed adopts the half-made issue (FR36's model)."""
    world.github.search_delay = 60.0
    world.settles = 120.0
    link_id = await _creating(engine, world, await _case(engine, world))
    pid: list[int] = []
    if point == TRACKER_POINT:
        world.github.after_commit = kill_after_commit(
            "create_issue", lambda: pid[0] if pid else None
        )

    exit_code = run_child(
        dsn=ASYNC_DSN,
        key=KEY,
        base_url=world.github.base_url,
        point=None if point == TRACKER_POINT else point,
        settles=world.settles,
        started=pid.append,
        org_id=world.ids["org_id"],
        connection_id=world.ids["connection_id"],
    )
    assert exit_code == KILLED
    world.github.after_commit = None
    # The killed run's lease (taken on the real clock) lapses, and the list's delay passes.
    world.clock.advance(timedelta(seconds=sync.LEASE_SECONDS + 60))
    await _sync(engine, world)

    assert len(world.github.issues_in("acme/api")) == 1
    link = await _link(engine, link_id)
    assert link.sync_status == "in_sync"
    [issue] = world.github.issues_in("acme/api")
    assert link.external_id == str(issue.id)


async def test_a_retry_before_the_list_settles_waits_rather_than_creating(
    engine: AsyncEngine, world: World
) -> None:
    world.github.search_delay = 60.0
    world.settles = 120.0
    link_id = await _creating(engine, world, await _case(engine, world))
    world.github.drop_after_commit.add("create_issue")
    await _sync(engine, world)
    world.github.drop_after_commit.clear()
    assert len(world.github.issues_in("acme/api")) == 1

    # The dropped reply read as a failure: the run paused, and backed off about a minute.
    world.clock.advance(timedelta(seconds=90))
    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert link.sync_status == "creating"
    assert link.next_sync_at == link.first_attempt_at + timedelta(seconds=120)

    world.clock.advance(timedelta(seconds=30))
    await _sync(engine, world)
    assert (await _link(engine, link_id)).sync_status == "in_sync"
    assert len(world.github.issues_in("acme/api")) == 1


# ------------------------------------------------------------- the comparison (AC45)


async def test_row_1_running_twice_changes_nothing(engine: AsyncEngine, world: World) -> None:
    finding = await _case(engine, world)
    link_id, _ = await _synced(engine, world, finding)
    await _sync(engine, world)
    before = (await _link(engine, link_id), await _finding(engine, finding))
    world.clock.advance(ACTIVE_POLL)
    await _sync(engine, world)
    after = (await _link(engine, link_id), await _finding(engine, finding))

    assert before[0].seen_state == after[0].seen_state == "open"
    assert before[1].case_state == after[1].case_state == "investigating"
    assert await _events(engine, finding) == []
    assert _writes(world.github) == []


@pytest.mark.parametrize(
    ("comment", "reason", "words"),
    [
        ("resolved", "completed", "was resolved"),
        ("new_normal", "completed", "the new normal"),
        ("false_alarm", "not_planned", "a false alarm"),
    ],
)
async def test_row_2_a_resolve_closes_the_issue_then_comments_once(
    engine: AsyncEngine, world: World, comment: str, reason: str, words: str
) -> None:
    finding = await _case(engine, world, case_state="resolved", resolution=comment)
    link_id, key = await _synced(engine, world, finding, wanted="closed", comment=comment)

    await _sync(engine, world)

    issue = world.github.issue(key)
    assert (issue.state, issue.state_reason) == ("closed", reason)
    assert len(issue.comments) == 1 and words in issue.comments[0]
    [patch, post] = _writes(world.github)
    assert patch[0] == "PATCH" and post[0] == "POST" and post[1].endswith("/comments")
    link = await _link(engine, link_id)
    assert (link.seen_state, link.wanted_state, link.comment_wanted) == ("closed", None, None)
    assert link.comment_written_at == world.clock.now
    assert link.next_sync_at == world.clock.now + RESOLVED_POLL

    world.clock.advance(RESOLVED_POLL)
    await _sync(engine, world)
    assert len(world.github.issue(key).comments) == 1


async def test_row_2_a_reopen_reopens_the_issue_with_no_comment(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    _, key = await _synced(engine, world, finding, seen="closed", wanted="open")
    await _sync(engine, world)
    assert world.github.issue(key).state == "open"
    assert world.github.issue(key).comments == []


async def test_row_3_our_earlier_write_landed(engine: AsyncEngine, world: World) -> None:
    finding = await _case(engine, world, case_state="resolved")
    link_id, _ = await _synced(engine, world, finding, wanted="closed", issue_state="closed")
    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.seen_state, link.wanted_state) == ("closed", None)
    assert not [call for call in _writes(world.github) if call[0] == "PATCH"]


async def test_row_4_reopened_there_reopens_the_case_and_restarts_its_clock(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, case_state="resolved", activity_ago=timedelta(days=3))
    link_id, key = await _synced(engine, world, finding, seen="closed")
    world.github.reopen(key)

    await _sync(engine, world)

    row = await _finding(engine, finding)
    assert (row.case_state, row.case_activity_at) == ("investigating", world.clock.now)
    assert [kind for kind, _, _ in await _events(engine, finding)] == [
        "issue_reopened",
        "case_reopened",
    ]
    link = await _link(engine, link_id)
    assert (link.seen_state, link.next_sync_at) == ("open", world.clock.now + ACTIVE_POLL)
    assert await _one(
        engine,
        "SELECT count(*) AS n FROM audit_log WHERE org_id = :org AND action = 'case.reopened' "
        "AND actor_user_id IS NULL",
        org=world.tenant.org_id,
    ) == (1,)


async def test_row_4b_reopened_there_during_a_hold_ends_it_and_nothing_else(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, signal="open", activity_ago=timedelta(days=3))
    link_id, key = await _synced(engine, world, finding, seen="closed", held=True)
    world.github.reopen(key)
    before = await _finding(engine, finding)

    await _sync(engine, world)

    link = await _link(engine, link_id)
    assert (link.seen_state, link.tracker_closed_at) == ("open", None)
    after = await _finding(engine, finding)
    assert after.case_activity_at == before.case_activity_at  # D92
    assert after.case_state == "investigating"
    assert [kind for kind, _, _ in await _events(engine, finding)] == ["issue_reopened"]
    assert await _emails(engine, finding) == []


async def test_row_5a_closed_there_with_the_signal_open_holds_the_case(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, signal="open")
    link_id, key = await _synced(engine, world, finding)
    world.github.close(key, "not_planned")

    await _sync(engine, world)

    assert (await _finding(engine, finding)).case_state == "investigating"
    link = await _link(engine, link_id)
    assert (link.seen_state, link.tracker_closed_at) == ("closed", world.clock.now)
    assert await _events(engine, finding) == [
        ("issue_closed", "tracker", {"link_id": str(link_id), "close_reason": "not_planned"})
    ]
    assert await _emails(engine, finding) == ["tracker_closed_signal_open"]

    # Row 5b: once the signal closes, the next sync resolves it, with a second email.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE findings SET state = 'closed', closed_at = :now, "
                "closed_reason = 'recovered' WHERE id = :id"
            ),
            {"now": world.clock.now, "id": finding},
        )
    world.clock.advance(ACTIVE_POLL)
    await _sync(engine, world)

    row = await _finding(engine, finding)
    assert (row.case_state, row.case_resolution) == ("resolved", "tracker_closed")
    assert (await _link(engine, link_id)).tracker_closed_at is None
    assert await _emails(engine, finding) == ["tracker_closed_signal_open", "tracker_closed"]


async def test_a_hold_leaves_an_open_joined_finding_on_the_case(
    engine: AsyncEngine, world: World
) -> None:
    root = await _case(engine, world)
    joined = await _case(engine, world, signal="open", case_state=None)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE findings SET root_finding_id = :root WHERE id = :id"),
            {"root": root, "id": joined},
        )
    _, key = await _synced(engine, world, root)
    world.github.close(key)

    await _sync(engine, world)

    assert (await _finding(engine, root)).case_state == "investigating"
    assert (await _finding(engine, joined)).root_finding_id == root


async def test_row_5c_closed_there_with_the_signal_closed_resolves(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id, key = await _synced(engine, world, finding)
    world.github.close(key, "duplicate")

    await _sync(engine, world)

    row = await _finding(engine, finding)
    assert (row.case_state, row.case_resolution, row.case_resolved_by) == (
        "resolved",
        "tracker_closed",
        None,
    )
    events = await _events(engine, finding)
    assert events[0] == (
        "issue_closed",
        "tracker",
        {"link_id": str(link_id), "close_reason": "duplicate"},
    )
    assert events[-1] == ("case_resolved", "tracker", {"resolution": "tracker_closed"})
    assert await _emails(engine, finding) == ["tracker_closed"]
    assert world.github.issue(key).comments == []


async def test_row_6_a_case_resolved_leaving_the_issue_open_records_a_later_close_only(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, case_state="resolved")
    link_id, key = await _synced(engine, world, finding)
    world.github.close(key)

    await _sync(engine, world)

    assert (await _link(engine, link_id)).seen_state == "closed"
    assert await _events(engine, finding) == []
    assert await _emails(engine, finding) == []


async def test_row_7_a_reopen_another_case_blocks_needs_attention(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, case_state="resolved")
    await _case(engine, world, signal="open")  # the active case on the same service
    link_id, key = await _synced(engine, world, finding, seen="closed")
    world.github.reopen(key)

    await _sync(engine, world)

    assert (await _finding(engine, finding)).case_state == "resolved"
    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure, link.seen_state) == (
        "needs_attention",
        "reopen_blocked",
        "open",
    )
    assert [kind for kind, _, _ in await _events(engine, finding)] == ["issue_reopened"]
    world.clock.advance(RESOLVED_POLL)
    await _sync(engine, world)
    assert (await _link(engine, link_id)).sync_status == "needs_attention"


async def test_row_8_a_deleted_issue_is_gone_and_no_longer_read(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id, key = await _synced(engine, world, finding)
    world.github.delete(key)

    await _sync(engine, world)

    link = await _link(engine, link_id)
    assert (link.sync_status, link.next_sync_at) == ("gone", None)
    assert (await _finding(engine, finding)).case_state == "investigating"
    assert [kind for kind, _, _ in await _events(engine, finding)] == ["issue_gone"]


async def test_row_9_missing_from_a_readable_repository_is_read_daily_for_a_week(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id, key = await _synced(engine, world, finding)
    world.github.issues.remove(world.github.issue(key))

    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.sync_status, link.next_sync_at) == ("gone", world.clock.now + timedelta(days=1))

    world.clock.advance(timedelta(days=7, minutes=1))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_links SET next_sync_at = :now WHERE id = :id"),
            {"now": world.clock.now, "id": link_id},
        )
    await _sync(engine, world)
    assert (await _link(engine, link_id)).next_sync_at is None
    assert [kind for kind, _, _ in await _events(engine, finding)] == ["issue_gone"]


async def test_row_10_a_repository_removed_and_restored_resumes(
    engine: AsyncEngine, world: World
) -> None:
    """AC52: removed from the installation, its links need attention; restored, the
    hourly refresh makes them due and the next run finds them in step."""
    finding = await _case(engine, world)
    link_id, _ = await _synced(engine, world, finding)
    world.github.remove_repository("acme/api")

    await _sync(engine, world)

    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure) == ("needs_attention", "repository_unreachable")
    place = await _one(
        engine,
        "SELECT is_reachable FROM connection_repositories WHERE id = :id",
        id=world.api_place,
    )
    assert place == (False,)
    assert (await _finding(engine, finding)).case_state == "investigating"

    world.github.restore_repository("acme/api")
    await tasks.refresh(
        engine,
        keys=KEYS,
        endpoint=GitHubEndpoint(base_url=world.github.base_url, private_allowed=True),
        now=world.clock.now,
        **world.ids,
    )
    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure) == ("in_sync", None)


async def test_row_11_a_transfer_into_a_connected_repository_is_followed(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id, key = await _synced(engine, world, finding)
    moved = world.github.transfer(key, "acme/web")

    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.external_key, link.external_id, link.place_id) == (
        f"acme/web#{moved.number}",
        str(moved.id),
        world.web_place,
    )
    world.github.close(f"acme/web#{moved.number}")
    await _sync(engine, world)
    assert (await _finding(engine, finding)).case_state == "resolved"


async def test_row_11_a_transfer_out_of_reach_needs_attention(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world)
    link_id, key = await _synced(engine, world, finding)
    world.github.transfer(key, "elsewhere/api")

    await _sync(engine, world)

    link = await _link(engine, link_id)
    assert (link.sync_status, link.last_failure, link.external_key) == (
        "needs_attention",
        "moved_out_of_reach",
        key,
    )
    assert (await _finding(engine, finding)).case_state == "investigating"


# -------------------------------------------- a close and its comment, killed (AC87)


@pytest.mark.parametrize("point", COMMENT_POINTS)
async def test_a_kill_between_close_and_comment_leaves_at_most_one_comment(
    engine: AsyncEngine, world: World, point: str
) -> None:
    finding = await _case(engine, world, case_state="resolved")
    link_id, key = await _synced(engine, world, finding, wanted="closed", comment="resolved")

    exit_code = run_child(
        dsn=ASYNC_DSN,
        key=KEY,
        base_url=world.github.base_url,
        point=point,
        org_id=world.ids["org_id"],
        connection_id=world.ids["connection_id"],
    )
    assert exit_code == KILLED
    assert world.github.issue(key).state == "closed"
    assert world.github.issue(key).comments == []

    world.clock.advance(timedelta(seconds=sync.LEASE_SECONDS + 60))
    await _sync(engine, world)
    await _sync(engine, world)

    comments = world.github.issue(key).comments
    assert len(comments) == (1 if point == "after_close_before_comment_commit" else 0)
    link = await _link(engine, link_id)
    assert (link.seen_state, link.wanted_state, link.comment_wanted) == ("closed", None, None)


# ------------------------------------------------------- pace and failures (AC49, AC86)


async def test_writes_are_a_second_apart(engine: AsyncEngine, world: World) -> None:
    for service in ("checkout", "payments"):
        finding = await _case(engine, world, service=service, case_state="resolved")
        await _synced(engine, world, finding, wanted="closed")
    await _sync(engine, world)
    # The clock does not move inside a run, so each write after the first waits a second.
    assert world.pauses == [1.0]


async def test_a_rate_limit_pauses_the_connection_for_as_long_as_it_asked(
    engine: AsyncEngine, world: World
) -> None:
    first = await _case(engine, world)
    second = await _case(engine, world, service="payments")
    await _synced(engine, world, first)
    untouched, _ = await _synced(engine, world, second)
    world.github.fail("read_issue", 429, headers={"retry-after": "120"})

    await _sync(engine, world)

    connection = await _connection(engine, world)
    assert connection.paused_until == world.clock.now + timedelta(seconds=120)
    assert (await _link(engine, untouched)).last_synced_at is None
    reads = [r for r in world.github.site.requests if "/issues/" in r.path and r.method == "GET"]
    assert len(reads) == 1

    # While paused, a run makes no call.
    world.github.site.requests.clear()
    await _sync(engine, world)
    assert not [r for r in world.github.site.requests if r.path.startswith("/repos/")]


async def test_a_dead_tracker_ends_the_run_at_the_first_failure(
    engine: AsyncEngine, world: World
) -> None:
    first = await _case(engine, world)
    second = await _case(engine, world, service="payments")
    failed, _ = await _synced(engine, world, first)
    untouched, _ = await _synced(engine, world, second)
    world.github.fail("read_issue", 500)

    await _sync(engine, world)

    link = await _link(engine, failed)
    assert (link.sync_status, link.failures, link.last_failure) == ("waiting", 1, "server_error")
    assert link.next_sync_at > world.clock.now
    assert (await _link(engine, untouched)).last_synced_at is None
    connection = await _connection(engine, world)
    assert connection.paused_until > world.clock.now
    assert connection.consecutive_failures == 1


async def test_a_disconnect_between_two_links_stops_the_next_write(
    engine: AsyncEngine, world: World
) -> None:
    """AC37: the job reads the connection's state before each call; the one already on
    the wire lands, the next does not go."""
    first = await _case(engine, world, case_state="resolved")
    second = await _case(engine, world, service="payments", case_state="resolved")
    _, first_key = await _synced(engine, world, first, wanted="closed")
    _, second_key = await _synced(engine, world, second, wanted="closed")

    async def disconnect(_: float) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE connections SET state = 'disconnected', sealed_credential = NULL "
                    "WHERE id = :id"
                ),
                {"id": world.connection_id},
            )

    await sync.sync_connection(
        engine,
        keys=KEYS,
        tracker_of=_tracker_of(world),
        clock=world.clock,
        pause=disconnect,
        **world.ids,
    )

    states = {world.github.issue(first_key).state, world.github.issue(second_key).state}
    assert states == {"open", "closed"}
    assert len([call for call in _writes(world.github) if call[0] == "PATCH"]) == 1


# ------------------------------------------------------- what else it must survive


async def test_a_recovery_leaves_the_link_alone_and_writes_nothing(
    engine: AsyncEngine, world: World
) -> None:
    """AC4: a signal closing is not an act on the case or the issue."""
    finding = await _case(engine, world, signal="open")
    link_id, _ = await _synced(engine, world, finding)
    await _sync(engine, world)
    before = await _link(engine, link_id)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE findings SET state = 'closed', closed_at = :now, "
                "closed_reason = 'recovered' WHERE id = :id"
            ),
            {"now": world.clock.now, "id": finding},
        )
    assert (await _link(engine, link_id)) == before
    world.clock.advance(ACTIVE_POLL)
    await _sync(engine, world)
    assert (await _finding(engine, finding)).case_state == "investigating"
    assert _writes(world.github) == []


async def test_a_resolve_while_sync_was_stopped_reaches_the_tracker_later(
    engine: AsyncEngine, world: World
) -> None:
    """AC55: the resolve happened with no run; days later the first run carries it."""
    finding = await _case(engine, world, case_state="resolved", resolved_ago=timedelta(days=2))
    _, key = await _synced(engine, world, finding, wanted="closed", comment="resolved")
    await _sync(engine, world)
    assert world.github.issue(key).state == "closed"


async def test_a_replaced_key_resumes_the_same_issue(engine: AsyncEngine, world: World) -> None:
    """AC75: no new link, no duplicate issue."""
    finding = await _case(engine, world, case_state="resolved")
    link_id, key = await _synced(engine, world, finding, wanted="closed")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connections SET state = 'needs_attention' WHERE id = :id"),
            {"id": world.connection_id},
        )
    await _sync(engine, world)
    assert world.github.issue(key).state == "open"

    # FR43: the key is re-sealed in place and the test passes: `working` again.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connections SET state = 'working', sealed_credential = :key WHERE id = :id"
            ),
            {
                "key": _box().seal(
                    world.github.private_key_pem,
                    owner=connection_secret_owner(
                        str(world.tenant.org_id), str(world.connection_id)
                    ),
                ),
                "id": world.connection_id,
            },
        )
    await _sync(engine, world)
    assert world.github.issue(key).state == "closed"
    assert len(world.github.issues_in("acme/api")) == 1
    count = await _one(
        engine, "SELECT count(*) FROM finding_links WHERE finding_id = :id", id=finding
    )
    assert count == (1,)
    assert (await _link(engine, link_id)).sync_status == "in_sync"


async def test_a_no_activity_case_reopened_there_stays_90_days_with_no_second_comment(
    engine: AsyncEngine, world: World
) -> None:
    """AC96."""
    finding = await _case(
        engine,
        world,
        case_state="resolved",
        resolution="no_activity",
        activity_ago=timedelta(days=90),
    )
    link_id, key = await _synced(engine, world, finding, seen="closed")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_links SET idle_warning_at = :at WHERE id = :id"),
            {"at": world.clock.now - timedelta(days=14), "id": link_id},
        )
    world.github.reopen(key)

    await _sync(engine, world)

    row = await _finding(engine, finding)
    assert row.case_state == "investigating"
    assert row.case_activity_at + CASE_IDLE_LIMIT == world.clock.now + CASE_IDLE_LIMIT
    assert world.github.issue(key).comments == []


async def test_the_idle_warning_comment_carries_its_date(engine: AsyncEngine, world: World) -> None:
    finding = await _case(engine, world, activity_ago=timedelta(days=76))
    _, key = await _synced(engine, world, finding, comment="idle_warning")
    await _sync(engine, world)
    ends = (world.clock.now - timedelta(days=76) + CASE_IDLE_LIMIT).date()
    assert world.github.issue(key).comments == [comment_text("idle_warning", ends_on=ends).markdown]
    assert world.github.issue(key).state == "open"


async def test_an_idle_warning_owed_on_a_case_that_then_ended_is_not_posted(
    engine: AsyncEngine, world: World
) -> None:
    """AC97: none when the case then ends."""
    finding = await _case(engine, world, case_state="resolved", resolution="no_activity")
    link_id, key = await _synced(engine, world, finding, comment="idle_warning")
    await _sync(engine, world)
    assert world.github.issue(key).comments == []
    assert (await _link(engine, link_id)).comment_wanted is None


async def test_a_link_past_30_days_after_its_case_resolved_is_no_longer_followed(
    engine: AsyncEngine, world: World
) -> None:
    finding = await _case(engine, world, case_state="resolved", resolved_ago=timedelta(days=31))
    link_id, key = await _synced(engine, world, finding)
    world.github.close(key)
    await _sync(engine, world)
    link = await _link(engine, link_id)
    assert (link.sync_status, link.next_sync_at, link.seen_state) == ("not_followed", None, "open")
    assert not [r for r in world.github.site.requests if r.path.startswith("/repos/")]


async def test_a_job_naming_another_organization_s_connection_does_nothing(
    engine: AsyncEngine, world: World
) -> None:
    """AC129: the job binds its own `org_id`; another's connection is no row."""
    finding = await _case(engine, world, case_state="resolved")
    _, key = await _synced(engine, world, finding, wanted="closed")
    await _sync(engine, world, org_id=str(uuid.uuid4()), connection_id=str(world.connection_id))
    assert world.github.site.requests == []
    assert world.github.issue(key).state == "open"


async def test_a_held_lease_stops_a_second_run(engine: AsyncEngine, world: World) -> None:
    finding = await _case(engine, world, case_state="resolved")
    _, key = await _synced(engine, world, finding, wanted="closed")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connections SET sync_lease_until = :until WHERE id = :id"),
            {"until": world.clock.now + timedelta(minutes=1), "id": world.connection_id},
        )
    assert await _sync(engine, world) == []
    assert world.github.issue(key).state == "open"
    # A finished run lets go of its lease.
    world.clock.advance(timedelta(minutes=2))
    await _sync(engine, world)
    assert (await _connection(engine, world)).sync_lease_until is None


async def test_a_successful_run_returns_a_failing_connection_to_working(
    engine: AsyncEngine, world: World
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connections SET state = 'failing', consecutive_failures = 6, "
                "failing_since = :at, failure_told_at = :at, last_failure = 'server_error' "
                "WHERE id = :id"
            ),
            {"at": world.clock.now - timedelta(hours=1), "id": world.connection_id},
        )
    await _synced(engine, world, await _case(engine, world))
    await _sync(engine, world)
    connection = await _connection(engine, world)
    assert (connection.state, connection.consecutive_failures, connection.failure_told_at) == (
        "working",
        0,
        None,
    )


# -------------------------------------------------------------------- the sweep


async def _jobs(engine: AsyncEngine, world: World, task: str) -> int:
    row = await _one(
        engine,
        "SELECT count(*) FROM jobs WHERE org_id = :org AND task = :task",
        org=world.tenant.org_id,
        task=task,
    )
    return int(row[0])


async def test_the_sweep_adds_one_sync_job_however_often_it_runs(
    engine: AsyncEngine, world: World
) -> None:
    await _synced(engine, world, await _case(engine, world))
    for _ in range(3):
        await sync.sweep_connections(engine, now=world.clock.now)
    assert await _jobs(engine, world, SYNC_CONNECTION_TASK) == 1
    assert await _jobs(engine, world, REFRESH_CONNECTION_TASK) == 0


async def test_the_sweep_adds_no_sync_job_with_nothing_due(
    engine: AsyncEngine, world: World
) -> None:
    link_id, _ = await _synced(engine, world, await _case(engine, world))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_links SET next_sync_at = :later WHERE id = :id"),
            {"later": world.clock.now + timedelta(minutes=5), "id": link_id},
        )
    await sync.sweep_connections(engine, now=world.clock.now)
    assert await _jobs(engine, world, SYNC_CONNECTION_TASK) == 0


async def test_the_sweep_refreshes_each_connection_hourly(
    engine: AsyncEngine, world: World
) -> None:
    await sync.sweep_connections(engine, now=world.clock.now + timedelta(minutes=59))
    assert await _jobs(engine, world, REFRESH_CONNECTION_TASK) == 0
    for _ in range(2):
        await sync.sweep_connections(engine, now=world.clock.now + timedelta(minutes=61))
    assert await _jobs(engine, world, REFRESH_CONNECTION_TASK) == 1


async def test_the_sweep_leaves_a_disconnected_or_paused_connection_alone(
    engine: AsyncEngine, world: World
) -> None:
    await _synced(engine, world, await _case(engine, world))
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE connections SET paused_until = :until WHERE id = :id"),
            {"until": world.clock.now + timedelta(hours=2, minutes=5), "id": world.connection_id},
        )
    await sync.sweep_connections(engine, now=world.clock.now + timedelta(hours=2))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connections SET paused_until = NULL, state = 'disconnected', "
                "sealed_credential = NULL WHERE id = :id"
            ),
            {"id": world.connection_id},
        )
    await sync.sweep_connections(engine, now=world.clock.now + timedelta(hours=2))
    assert await _jobs(engine, world, SYNC_CONNECTION_TASK) == 0
    assert await _jobs(engine, world, REFRESH_CONNECTION_TASK) == 0


async def test_a_link_whose_repository_row_is_gone_needs_attention_and_the_run_goes_on(
    engine: AsyncEngine, world: World
) -> None:
    """A place removed from the connection leaves its links as text (`SET NULL`): they
    say so and stop being read, and the next link is still synced."""
    orphan = await _case(engine, world)
    orphan_link, _ = await _synced(engine, world, orphan, repository="acme/web")
    creating = await _creating(
        engine, world, await _case(engine, world, service="search"), place=world.web_place
    )
    healthy, _ = await _synced(engine, world, await _case(engine, world, service="payments"))
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM connection_repositories WHERE id = :id"), {"id": world.web_place}
        )

    await _sync(engine, world)

    gone = await _link(engine, orphan_link)
    assert (gone.sync_status, gone.last_failure, gone.next_sync_at) == (
        "needs_attention",
        "repository_unreachable",
        None,
    )
    refused = await _link(engine, creating)
    assert (refused.sync_status, refused.last_failure) == ("refused", "repository_unreachable")
    assert (await _link(engine, healthy)).last_synced_at == world.clock.now
    # Only the issue the test arranged: nothing was created for the refused link.
    assert len(world.github.issues_in("acme/web")) == 1
