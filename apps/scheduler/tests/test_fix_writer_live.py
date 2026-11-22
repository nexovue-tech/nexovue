"""The draft job, the draft check and **Delete this branch**, against the simulated GitHub (F5
plan T7.4's check half, T7.5, T7.10; spec FR22, FR23, BR8, BR13, BR14, AC19, AC22, AC24,
AC26, AC26a, AC27, AC27a, AC48, AC66).

**As shipped, write access is off in code** (`write_gate.WRITE_ACCESS_GO` is None, ADR-097):
a `draft` check is refused `writes_off` and the draft job puts an `approved` row back with
`writes_off`, **with no request of any kind reaching GitHub**, read from the fake's own log.

**With the gate forced on inside a test** (a `WriteGo` built here and passed to the job, as
the task passes the module's constant), the whole path runs: the person's own `draft` check
re-reads the files and stores the digest; the job re-reads and re-checks them again, and
writes exactly the checked change: one blob per changed file, one tree on the default
branch's head with nothing else changed, one commit with both skip instructions, the one
`nexovue/suggestion-<id>` branch, and a draft pull request whose body is all template.
Then: a crash after each write and a retry that reuses what was stored; GitHub's 422 on a
second pull request adopted; a branch not Nexovue's refused; a file changed after the
check stopping it with nothing written; a refused draft never retried as a pull request
that is not one; a withdrawal mid-job stopping every later write; the sweep returning a
job that never finished.

Every test builds and drops its own organization; the clock is the test's own, read when it
starts. No job is committed into `jobs`: each job's code is called directly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import postgres_reachable
from github_git_data import GitData
from live import require_stack
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id
from nexovue_platform.fixes.pack import owner_lists_hash
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.pull_request_text import (
    SKIP_CHECKS_TRAILER,
    SKIP_CI_MARKER,
    WHAT_THIS_IS,
)
from nexovue_platform.fixes.records import (
    FIX_BRANCH_DELETED,
    FIX_PULL_REQUEST_OPENED,
    REPOSITORY_READ,
    fix_branch,
)
from nexovue_platform.fixes.write_gate import WRITE_ACCESS_GO, WriteGo
from recording_site import json_reply
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    build_world,
    drop_world,
    fix_row,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_check, fix_sweep, fix_writer
from nexovue_scheduler.connections.fix_github import FixGitHub
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_writer_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

# A go built only here, so the gate is on inside these tests alone. The product passes
# `write_gate.WRITE_ACCESS_GO`, which is None.
TEST_GO: Final = WriteGo(
    given_on=date(2027, 1, 4),
    safety_gates_passed_on=("a test's own option",),
    buyers_answer="a test's own record",
    terms="a test's own terms",
    drafts_from_stack_alone=False,
    enterprise_server_before_step_7=False,
)
FOLDER: Final = "services/shop"
PATH: Final = f"{FOLDER}/app/pay.py"
OTHER: Final = f"{FOLDER}/app/other.py"
READ: Final = (
    "import json\n"
    "\n"
    "def charge(order, gateway):\n"
    "    amount = order.total\n"
    "    if amount < 0:\n"
    "        raise ValueError('negative')\n"
    "    return gateway.charge(amount)\n"
)
OLD: Final = "    return gateway.charge(amount)\n"
NEW: Final = "    return gateway.charge(amount, retries=2)\n"
# The model's words: they must never reach GitHub (BR10, AC27).
WHY: Final = "MODEL-PROSE-MARKER the call needs a retry count."
GITHUB_USER: Final = 9301
LOGIN: Final = "ada-lovelace"
BASE_URL: Final = "https://nexovue.example"
WRITE_GRANT: Final = {"contents": "write", "metadata": "read", "pull_requests": "write"}
_WRITE_METHODS: Final = frozenset({"POST", "PATCH", "DELETE", "PUT"})


@pytest.fixture
def now() -> datetime:
    """This test's clock, read when it starts: the stack's own sweep reads the real clock,
    so a row dated in the past is one it may expire or end under the test."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def fake() -> Iterator[FakeGitHub]:
    with serve_fake_github() as served:
        yield served


@pytest.fixture
async def world(engine: AsyncEngine, fake: FakeGitHub, now: datetime) -> AsyncIterator[SourceWorld]:
    made = await build_world(engine, fake, folder=FOLDER, now=now)
    fake.seed_files(REPOSITORY, {PATH: READ, OTHER: "x = 1\n"})
    fake.add_user(GITHUB_USER, LOGIN)
    fake.grant(REPOSITORY, LOGIN, "write")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connections SET granted_permissions = CAST(:granted AS jsonb) "
                "WHERE id = :id"
            ),
            {"granted": json.dumps(WRITE_GRANT), "id": made.connection_id},
        )
        await connection.execute(
            text(
                "INSERT INTO fix_repository_settings (org_id, connection_repository_id, "
                "drafts_allowed, set_at) VALUES (:org, :repository, true, :now)"
            ),
            {"org": made.org_id, "repository": made.repository_id, "now": now},
        )
        await connection.execute(
            text(
                "INSERT INTO github_user_links (user_id, org_id, github_user_id, github_login) "
                "VALUES (:user, :org, :github, :login)"
            ),
            {"user": made.user_id, "org": made.org_id, "github": GITHUB_USER, "login": LOGIN},
        )
        await connection.execute(
            text(
                """
                INSERT INTO finding_explanations (org_id, project_id, finding_id, version,
                    read_at, fingerprint, evidence, aliases, summary, model_input, likely_cause)
                VALUES (:org, :project, :finding, 1, :at, :fingerprint,
                    CAST('{"items": []}' AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb),
                    NULL, CAST(:cause AS jsonb))
                """
            ),
            {
                "org": made.org_id,
                "project": made.tenant.project_id,
                "finding": made.finding_id,
                "at": now - timedelta(minutes=10),
                "fingerprint": "7" * 64,
                "cause": json.dumps({"rule": "signed_deploy", "evidence_id": "deploy:d-1"}),
            },
        )
    try:
        yield made
    finally:
        await drop_world(engine, made)


def _edits() -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, NEW, WHY, ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


async def _proposed(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    files_read = {
        "commit": "c" * 40,
        "commit_known": False,
        "round": 1,
        "files": [
            {
                "ref": "file_1",
                "path": PATH,
                "blob_sha": git_blob_id(READ),
                "mode": "100644",
                "role": "stack",
                "why": {"kind": "stack", "line": 7},
                "moved_on_default": False,
                "lines_sent": None,
                "bytes": len(READ),
            }
        ],
        "left_out": [],
    }
    return await source_fix(
        engine,
        world,
        candidates=[],
        now=now,
        state="proposed",
        edits=json.dumps(_edits()),
        files_read=json.dumps(files_read),
        checks_version=CHECKS_VERSION,
        extra_list_hash=owner_lists_hash(OwnerLists()),
        explanation_version=1,
        finished_at=now,
        result_recorded_at=now,
    )


async def _draft_check(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime, *, go: Any
) -> Any:
    async with engine.begin() as connection:
        check_id = await connection.scalar(
            text(
                """
                INSERT INTO finding_fix_checks (org_id, fix_id, requested_by, purpose, status,
                    requested_at, expires_at)
                VALUES (:org, :fix, :user, 'draft', 'running', :now, :expires)
                RETURNING id
                """
            ),
            {
                "org": world.org_id,
                "fix": fix_id,
                "user": world.user_id,
                "now": now,
                "expires": now + timedelta(minutes=15),
            },
        )
    status = await fix_check.check_fix(
        engine,
        org_id=str(world.org_id),
        check_id=str(check_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=go,
        show_held_causes=True,
        now=now,
    )
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT * FROM finding_fix_checks WHERE id = :id"), {"id": check_id}
            )
        ).one()
    return status, row


async def _approve(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, digest: str, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET state = 'approved', state_changed_at = :now, "
                "approved_by = :user, approved_at = :now, approved_digest = :digest "
                "WHERE id = :id"
            ),
            {"now": now, "user": world.user_id, "digest": digest, "id": fix_id},
        )


async def _approved(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    """A suggestion approved from the person's own passed check, as the click leaves it."""
    fix_id = await _proposed(engine, world, now)
    status, check = await _draft_check(engine, world, fix_id, now, go=TEST_GO)
    assert status == "unchanged", (status, check.reason)
    await _approve(engine, world, fix_id, check.digest, now)
    return fix_id


async def _open(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime, *, go: Any = TEST_GO
) -> str | None:
    return await fix_writer.open_pull_request(
        engine,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=go,
        show_held_causes=True,
        base_url=BASE_URL,
        now=now,
    )


def _git(fake: FakeGitHub) -> GitData:
    return fake.git_of(REPOSITORY)


def _writes(fake: FakeGitHub) -> list[str]:
    """Every request that could change a repository, from the fake's own log: a write
    method on a repository route, or a token minted to write."""
    found = []
    for request in fake.site.requests:
        if request.method in _WRITE_METHODS and request.path.startswith("/repos/"):
            found.append(f"{request.method} {request.path}")
        if request.path.endswith("/access_tokens") and b'"write"' in request.body:
            found.append(f"write token {request.path}")
    return found


async def _audit(engine: AsyncEngine, world: SourceWorld, action: str) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT actor_user_id, detail FROM audit_log "
                        "WHERE org_id = :org AND action = :action ORDER BY at"
                    ),
                    {"org": world.org_id, "action": action},
                )
            ).all()
        )


# ------------------------------------------------------- as shipped: off in code


def test_the_gate_ships_off() -> None:
    assert WRITE_ACCESS_GO is None


async def test_as_shipped_a_draft_check_is_refused_with_no_request_to_github(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    before = len(fake.site.requests)

    status, check = await _draft_check(engine, world, fix_id, now, go=WRITE_ACCESS_GO)

    assert (status, check.status, check.reason) == ("refused", "refused", "writes_off")
    assert check.patch is None and check.digest is None
    assert len(fake.site.requests) == before
    assert (await fix_row(engine, world, fix_id)).state == "proposed"


async def test_as_shipped_the_draft_job_writes_nothing_and_reads_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    before = len(fake.site.requests)
    refs_before = dict(_git(fake).refs)

    assert await _open(engine, world, fix_id, now, go=WRITE_ACCESS_GO) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "writes_off")
    assert row.pull_request_branch is None and row.pull_request_commit_sha is None
    assert len(fake.site.requests) == before
    assert _writes(fake) == [] and fake.pulls_in(REPOSITORY) == []
    assert _git(fake).refs == refs_before


async def test_as_shipped_delete_this_branch_does_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    branch = fix_branch(fix_id)
    _git(fake).refs[branch] = _git(fake).refs["main"]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET pull_request_branch = :branch, "
                "pull_request_commit_sha = :sha, pull_request_state = 'not_opened' "
                "WHERE id = :id"
            ),
            {"branch": branch, "sha": _git(fake).refs["main"], "id": fix_id},
        )
    before = len(fake.site.requests)

    deleted = await fix_writer.delete_fix_branch(
        engine,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        requested_by=str(world.user_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=WRITE_ACCESS_GO,
        now=now,
    )

    assert deleted is None
    assert len(fake.site.requests) == before and branch in _git(fake).refs


# --------------------------------------------- with the gate forced on, in a test


async def test_ac22_a_click_makes_one_draft_with_exactly_the_checked_change(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    main_before = _git(fake).refs["main"]
    main_files = dict(_git(fake).flat(_git(fake).commits[main_before].tree))
    fix_id = await _approved(engine, world, now)

    assert await _open(engine, world, fix_id, now) == "opened"

    git = _git(fake)
    branch = fix_branch(fix_id)
    assert set(git.refs) == {"main", branch}
    assert git.refs["main"] == main_before
    commit = git.commits[git.refs[branch]]
    assert commit.parents == (main_before,)
    subject, _, trailer = commit.message.partition("\n\n")
    assert subject.endswith(SKIP_CI_MARKER) and trailer == SKIP_CHECKS_TRAILER
    files = dict(git.flat(commit.tree))
    assert set(files) == set(main_files)
    assert {path for path in files if files[path] != main_files[path]} == {PATH}
    assert git.blobs[files[PATH][1]].decode() == READ.replace(OLD, NEW)
    assert files[PATH][0] == "100644"
    [pull] = fake.pulls_in(REPOSITORY)
    assert pull.draft is True and pull.head_ref == branch and pull.base_ref == "main"
    assert pull.head_sha == git.refs[branch]
    assert WHAT_THIS_IS in pull.body and WHY not in pull.body and "MODEL-PROSE" not in pull.title
    assert f"Requested by GitHub user {LOGIN}, through Nexovue." in pull.body
    assert f"@{LOGIN}" not in pull.body
    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.pull_request_state, row.pull_request_number) == ("opened", "open", 1)
    assert row.pull_request_branch == branch
    assert row.pull_request_commit_sha == git.refs[branch]
    assert row.pull_request_url == pull_url(pull.number)
    [opened] = await _audit(engine, world, FIX_PULL_REQUEST_OPENED)
    assert opened.actor_user_id == world.user_id
    assert opened.detail == {"fix_id": str(fix_id), "pull_request_number": 1, "branch": branch}
    reads = [each.detail for each in await _audit(engine, world, REPOSITORY_READ)]
    assert [each["purpose"] for each in reads] == ["draft", "draft"]
    async with engine.connect() as connection:
        events = (
            await connection.execute(
                text("SELECT kind, detail FROM finding_events WHERE finding_id = :id"),
                {"id": world.finding_id},
            )
        ).all()
    assert ("fix_pull_request_opened", {"fix_id": str(fix_id), "step_count": 1}) in [
        (each.kind, each.detail) for each in events
    ]


def pull_url(number: int) -> str:
    return f"https://github.com/{REPOSITORY}/pull/{number}"


async def test_ac61_ci_on_open_commits_with_no_skip_marker(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE fix_repository_settings SET ci_on_open = true WHERE org_id = :org"),
            {"org": world.org_id},
        )
    fix_id = await _approved(engine, world, now)

    assert await _open(engine, world, fix_id, now) == "opened"

    message = _git(fake).commits[_git(fake).refs[fix_branch(fix_id)]].message
    assert SKIP_CI_MARKER not in message and SKIP_CHECKS_TRAILER not in message


@pytest.mark.parametrize("route", ["create_blob", "create_commit", "create_ref", "create_pull"])
@pytest.mark.parametrize("main", ["still", "moved"])
async def test_ac26_a_crash_after_each_write_retries_to_one_branch_and_one_draft(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    route: str,
    main: str,
) -> None:
    fix_id = await _approved(engine, world, now)
    fake.drop_after_commit = {route}

    with pytest.raises(Retry):
        await _open(engine, world, fix_id, now)
    stored = (await fix_row(engine, world, fix_id)).pull_request_commit_sha
    fake.drop_after_commit = set()
    if main == "moved":
        fake.seed_files(REPOSITORY, {OTHER: "x = 2\n"}, message="unrelated")

    assert await _open(engine, world, fix_id, now) == "opened"

    git = _git(fake)
    branch = fix_branch(fix_id)
    assert set(git.refs) == {"main", branch}
    assert len(fake.pulls_in(REPOSITORY)) == 1
    row = await fix_row(engine, world, fix_id)
    if stored is not None:
        # A commit already stored is reused and never rebuilt, whatever main did.
        assert row.pull_request_commit_sha == stored == git.refs[branch]
    assert dict(git.flat(git.commits[git.refs[branch]].tree))[PATH][1] == git_blob_id(
        READ.replace(OLD, NEW)
    )


async def test_ac51_a_pull_request_github_made_but_did_not_list_is_adopted_after_its_422(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first attempt's `POST /pulls` lands and its answer is lost; the retry's list
    does not show it yet, so it asks again, GitHub answers 422 for a second pull request
    on the branch, and the job adopts the one by `?head=`."""
    fix_id = await _approved(engine, world, now)
    fake.drop_after_commit = {"create_pull"}
    with pytest.raises(Retry):
        await _open(engine, world, fix_id, now)
    fake.drop_after_commit = set()
    real = FixGitHub.pulls_for
    calls = {"count": 0}

    def late_list(self: FixGitHub, token: str, key: str, fix: str) -> Any:
        calls["count"] += 1
        return [] if calls["count"] == 1 else real(self, token, key, fix)

    monkeypatch.setattr(FixGitHub, "pulls_for", late_list)

    assert await _open(engine, world, fix_id, now) == "opened"

    assert len(fake.pulls_in(REPOSITORY)) == 1
    creates = [r for r in fake.site.requests if r.method == "POST" and r.path.endswith("/pulls")]
    assert len(creates) == 2
    assert (await fix_row(engine, world, fix_id)).pull_request_number == 1


async def test_ac26_a_branch_whose_head_is_not_nexovue_s_is_refused_with_nothing_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    _git(fake).refs[fix_branch(fix_id)] = _git(fake).refs["main"]

    assert await _open(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "branch_not_ours")
    assert [w for w in _writes(fake) if not w.startswith("write token")] == []
    assert fake.pulls_in(REPOSITORY) == []


async def test_ac24_a_file_changed_after_the_check_stops_the_job_with_nothing_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    fake.seed_files(REPOSITORY, {PATH: "# moved on\n" + READ}, message="a change to the file")

    assert await _open(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "changed_since_check")
    assert [w for w in _writes(fake) if not w.startswith("write token")] == []
    assert set(_git(fake).refs) == {"main"}


async def test_ac27a_a_refused_draft_is_not_opened_never_retried_as_a_pull_request(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    fake.faults["create_pull"] = json_reply(
        {"message": "Draft pull requests are not supported in this repository."}, 422
    )

    assert await _open(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.pull_request_state, row.last_write_failure) == (
        "proposed",
        "not_opened",
        "no_draft_support",
    )
    assert row.pull_request_branch == fix_branch(fix_id)
    creates = [r for r in fake.site.requests if r.method == "POST" and r.path.endswith("/pulls")]
    assert len(creates) == 1 and json.loads(creates[0].body)["draft"] is True
    async with engine.connect() as connection:
        refused_at = await connection.scalar(
            text("SELECT draft_refused_at FROM fix_repository_settings WHERE org_id = :org"),
            {"org": world.org_id},
        )
    assert refused_at == now


async def test_ac48_a_withdrawal_mid_job_stops_every_later_write(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner withdraws just after the commit is stored: the branch is never made."""
    fix_id = await _approved(engine, world, now)
    real = fix_writer._DraftJob._store

    async def store_then_withdraw(self: Any, statement: Any, **values: Any) -> None:
        await real(self, statement, **values)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE fix_source_consents SET withdrawn_at = :now, "
                    "withdrawn_reason = 'owner' WHERE org_id = :org"
                ),
                {"now": now, "org": world.org_id},
            )

    monkeypatch.setattr(fix_writer._DraftJob, "_store", store_then_withdraw)

    assert await _open(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "consent_withdrawn")
    assert not any("/git/refs" in w or w.endswith("/pulls") for w in _writes(fake))
    assert set(_git(fake).refs) == {"main"} and fake.pulls_in(REPOSITORY) == []


async def test_ac23_a_person_without_write_access_is_refused_at_the_check(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.grant(REPOSITORY, LOGIN, "read")
    fix_id = await _proposed(engine, world, now)

    status, check = await _draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", "no_github_access")
    assert check.digest is None and check.patch is None
    assert (await fix_row(engine, world, fix_id)).approved_by is None
    assert _writes(fake) == []


@pytest.mark.parametrize(
    ("label", "statement", "reason"),
    [
        (
            "drafts not allowed",
            "UPDATE fix_repository_settings SET drafts_allowed = false WHERE org_id = :org",
            "drafts_not_allowed",
        ),
        (
            "the App without write",
            'UPDATE connections SET granted_permissions = \'{"contents": "read"}\' '
            "WHERE org_id = :org",
            "no_write_permission",
        ),
        (
            "GitHub refused a draft an hour ago",
            "UPDATE fix_repository_settings SET draft_refused_at = now() - interval '1 hour' "
            "WHERE org_id = :org",
            "no_draft_support",
        ),
        (
            "no signed deploy as the likely cause",
            'UPDATE finding_explanations SET likely_cause = \'{"rule": "traffic_rose"}\' '
            "WHERE org_id = :org",
            "needs_likely_cause",
        ),
    ],
)
async def test_ac60_each_draft_condition_refuses_the_check_before_any_github_call(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    label: str,
    statement: str,
    reason: str,
) -> None:
    fix_id = await _proposed(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(text(statement), {"org": world.org_id})
    before = len(fake.site.requests)

    status, check = await _draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", reason), label
    assert len(fake.site.requests) == before


async def test_br13_a_public_repository_without_an_allowance_is_refused_before_any_write(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.make_public(REPOSITORY)
    fix_id = await _proposed(engine, world, now)

    status, check = await _draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", "public_not_allowed")
    assert _writes(fake) == []


async def test_ac26a_the_sweep_returns_a_job_that_never_finished_and_a_late_run_stops(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET state_changed_at = :at WHERE id = :id"),
            {"at": now - timedelta(minutes=21), "id": fix_id},
        )

    await fix_sweep.sweep(engine, clock=lambda: now, only_org=str(world.org_id))

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "did_not_finish")
    before = len(fake.site.requests)
    assert await _open(engine, world, fix_id, now) is None
    assert len(fake.site.requests) == before


async def test_ac26_attempts_used_up_go_back_to_proposed_naming_the_failure(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _approved(engine, world, now)
    fake.fail("create_pull", 503)
    token = registry._ATTEMPT.set(3)
    try:
        assert await _open(engine, world, fix_id, now) == "proposed"
    finally:
        registry._ATTEMPT.reset(token)

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "github_unavailable")
    # BR8: the branch stored before `POST /pulls` keeps the finding held (AC51).
    assert row.pull_request_branch == fix_branch(fix_id) and row.pull_request_state is None


async def _closed_with_branch(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime, *, state: str
) -> uuid.UUID:
    fix_id = await _approved(engine, world, now)
    assert await _open(engine, world, fix_id, now) == "opened"
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET pull_request_state = :state WHERE id = :id"),
            {"state": state, "id": fix_id},
        )
    return fix_id


async def _delete(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime) -> Any:
    return await fix_writer.delete_fix_branch(
        engine,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        requested_by=str(world.user_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=TEST_GO,
        now=now,
    )


async def test_ac66_delete_this_branch_deletes_only_its_own_branch_at_its_own_head(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _closed_with_branch(engine, world, fake, now, state="closed")

    assert await _delete(engine, world, fix_id, now) == "deleted"

    assert set(_git(fake).refs) == {"main"}
    [deleted] = await _audit(engine, world, FIX_BRANCH_DELETED)
    assert deleted.actor_user_id == world.user_id
    deletes = [r.path for r in fake.site.requests if r.method == "DELETE" and "/git/" in r.path]
    assert deletes == [f"/repos/{REPOSITORY}/git/refs/heads/{fix_branch(fix_id)}"]


@pytest.mark.parametrize("why", ["still open", "moved by someone", "no write access"])
async def test_ac66_delete_this_branch_refuses_an_open_draft_a_moved_head_and_a_reader(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime, why: str
) -> None:
    fix_id = await _closed_with_branch(
        engine, world, fake, now, state="open" if why == "still open" else "closed"
    )
    branch = fix_branch(fix_id)
    if why == "moved by someone":
        _git(fake).refs[branch] = _git(fake).refs["main"]
    if why == "no write access":
        fake.grant(REPOSITORY, LOGIN, "read")

    assert await _delete(engine, world, fix_id, now) is None

    assert branch in _git(fake).refs
    assert not [r for r in fake.site.requests if r.method == "DELETE" and "/git/" in r.path]


async def test_ac62_a_repository_public_by_the_job_gets_no_unconfirmed_note(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """Private at the click, public (and allowed) by the job: GitHub's live answer decides,
    and a note the requester never confirmed for a public body stays out (FR24a)."""
    fix_id = await _approved(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET requester_note = 'NOTE-MARKER-9' WHERE id = :id"),
            {"id": fix_id},
        )
        await connection.execute(
            text("UPDATE connection_repositories SET public_allowed_at = :now WHERE id = :id"),
            {"now": now, "id": world.repository_id},
        )
    fake.make_public(REPOSITORY)

    assert await _open(engine, world, fix_id, now) == "opened"

    [pull] = fake.pulls_in(REPOSITORY)
    assert "NOTE-MARKER-9" not in pull.body and LOGIN not in pull.body
