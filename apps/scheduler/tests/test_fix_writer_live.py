"""The draft job, the draft check and **Delete this branch**, against the simulated GitHub (F5
plan T7.4's check half, T7.5, T7.10; spec FR22, FR23, BR8, BR13, BR14, AC19, AC22, AC24,
AC26, AC26a, AC27, AC27a, AC48, AC66).

**With write access off** (the gate passed as None; ADR-097; the product ships with the
founder's go since 2026-11-28): a `draft` check is refused `writes_off` and the draft job puts
an `approved` row back with `writes_off`, **with no request of any kind reaching GitHub**, read
from the fake's own log.

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
from datetime import datetime, timedelta
from typing import Any

import fix_writer_fixtures as fixtures
import pytest
from alert_fixtures import postgres_reachable
from fix_writer_fixtures import (
    LOGIN,
    NEW,
    OLD,
    OTHER,
    PATH,
    READ,
    TEST_GO,
    WHY,
    approved_fix,
    ask_ci,
    audit_rows,
    draft_check,
    git_of,
    open_draft,
    opened_fix,
    proposed_fix,
    run_ci,
    writes_seen,
)
from live import require_stack
from nexovue_platform.fixes.diff import git_blob_id
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
from nexovue_platform.fixes.write_gate import WRITE_ACCESS_GO
from recording_site import json_reply
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    fix_row,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub

from nexovue_scheduler.connections import fix_sweep, fix_writer
from nexovue_scheduler.connections.fix_github import FixGitHub
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_writer_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

# The suite's fixtures: the test's clock, the simulated GitHub and the tenant built on it.
now = fixtures.now
fake = fixtures.fake
world = fixtures.world


# ------------------------------------------------------- as shipped: off in code


def test_the_gate_ships_on_by_the_founders_go() -> None:
    """T6.5, given 2026-11-28 (`write_gate.py`); the off path stays proved below with None."""
    assert WRITE_ACCESS_GO is not None


async def test_with_write_access_off_a_draft_check_is_refused_with_no_request_to_github(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await proposed_fix(engine, world, now)
    before = len(fake.site.requests)

    status, check = await draft_check(engine, world, fix_id, now, go=None)

    assert (status, check.status, check.reason) == ("refused", "refused", "writes_off")
    assert check.patch is None and check.digest is None
    assert len(fake.site.requests) == before
    assert (await fix_row(engine, world, fix_id)).state == "proposed"


async def test_with_write_access_off_the_draft_job_writes_nothing_and_reads_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    before = len(fake.site.requests)
    refs_before = dict(git_of(fake).refs)

    assert await open_draft(engine, world, fix_id, now, go=None) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "writes_off")
    assert row.pull_request_branch is None and row.pull_request_commit_sha is None
    assert len(fake.site.requests) == before
    assert writes_seen(fake) == [] and fake.pulls_in(REPOSITORY) == []
    assert git_of(fake).refs == refs_before


async def test_with_write_access_off_delete_this_branch_does_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await proposed_fix(engine, world, now)
    branch = fix_branch(fix_id)
    git_of(fake).refs[branch] = git_of(fake).refs["main"]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET pull_request_branch = :branch, "
                "pull_request_commit_sha = :sha, pull_request_state = 'not_opened' "
                "WHERE id = :id"
            ),
            {"branch": branch, "sha": git_of(fake).refs["main"], "id": fix_id},
        )
    before = len(fake.site.requests)

    deleted = await fix_writer.delete_fix_branch(
        world.jobs,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        requested_by=str(world.user_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=None,
        now=now,
    )

    assert deleted is None
    assert len(fake.site.requests) == before and branch in git_of(fake).refs


# --------------------------------------------- with the gate forced on, in a test


async def test_ac22_a_click_makes_one_draft_with_exactly_the_checked_change(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    main_before = git_of(fake).refs["main"]
    main_files = dict(git_of(fake).flat(git_of(fake).commits[main_before].tree))
    fix_id = await approved_fix(engine, world, now)

    assert await open_draft(engine, world, fix_id, now) == "opened"

    git = git_of(fake)
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
    [opened] = await audit_rows(engine, world, FIX_PULL_REQUEST_OPENED)
    assert opened.actor_user_id == world.user_id
    assert opened.detail == {"fix_id": str(fix_id), "pull_request_number": 1, "branch": branch}
    reads = [each.detail for each in await audit_rows(engine, world, REPOSITORY_READ)]
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
    fix_id = await approved_fix(engine, world, now)

    assert await open_draft(engine, world, fix_id, now) == "opened"

    message = git_of(fake).commits[git_of(fake).refs[fix_branch(fix_id)]].message
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
    fix_id = await approved_fix(engine, world, now)
    fake.drop_after_commit = {route}

    with pytest.raises(Retry):
        await open_draft(engine, world, fix_id, now)
    stored = (await fix_row(engine, world, fix_id)).pull_request_commit_sha
    fake.drop_after_commit = set()
    if main == "moved":
        fake.seed_files(REPOSITORY, {OTHER: "x = 2\n"}, message="unrelated")

    assert await open_draft(engine, world, fix_id, now) == "opened"

    git = git_of(fake)
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
    fix_id = await approved_fix(engine, world, now)
    fake.drop_after_commit = {"create_pull"}
    with pytest.raises(Retry):
        await open_draft(engine, world, fix_id, now)
    fake.drop_after_commit = set()
    real = FixGitHub.pulls_for
    calls = {"count": 0}

    def late_list(self: FixGitHub, token: str, key: str, fix: str) -> Any:
        calls["count"] += 1
        return [] if calls["count"] == 1 else real(self, token, key, fix)

    monkeypatch.setattr(FixGitHub, "pulls_for", late_list)

    assert await open_draft(engine, world, fix_id, now) == "opened"

    assert len(fake.pulls_in(REPOSITORY)) == 1
    creates = [r for r in fake.site.requests if r.method == "POST" and r.path.endswith("/pulls")]
    assert len(creates) == 2
    assert (await fix_row(engine, world, fix_id)).pull_request_number == 1


async def test_ac26_a_branch_whose_head_is_not_nexovue_s_is_refused_with_nothing_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    git_of(fake).refs[fix_branch(fix_id)] = git_of(fake).refs["main"]

    assert await open_draft(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "branch_not_ours")
    assert [w for w in writes_seen(fake) if not w.startswith("write token")] == []
    assert fake.pulls_in(REPOSITORY) == []


async def test_ac24_a_file_changed_after_the_check_stops_the_job_with_nothing_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    fake.seed_files(REPOSITORY, {PATH: "# moved on\n" + READ}, message="a change to the file")

    assert await open_draft(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "changed_since_check")
    assert [w for w in writes_seen(fake) if not w.startswith("write token")] == []
    assert set(git_of(fake).refs) == {"main"}


async def test_ac27a_a_refused_draft_is_not_opened_never_retried_as_a_pull_request(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    fake.faults["create_pull"] = json_reply(
        {"message": "Draft pull requests are not supported in this repository."}, 422
    )

    assert await open_draft(engine, world, fix_id, now) == "proposed"

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
    fix_id = await approved_fix(engine, world, now)
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

    assert await open_draft(engine, world, fix_id, now) == "proposed"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "consent_withdrawn")
    assert not any("/git/refs" in w or w.endswith("/pulls") for w in writes_seen(fake))
    assert set(git_of(fake).refs) == {"main"} and fake.pulls_in(REPOSITORY) == []


async def test_ac23_a_person_without_write_access_is_refused_at_the_check(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.grant(REPOSITORY, LOGIN, "read")
    fix_id = await proposed_fix(engine, world, now)

    status, check = await draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", "no_github_access")
    assert check.digest is None and check.patch is None
    assert (await fix_row(engine, world, fix_id)).approved_by is None
    assert writes_seen(fake) == []


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
    fix_id = await proposed_fix(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(text(statement), {"org": world.org_id})
    before = len(fake.site.requests)

    status, check = await draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", reason), label
    assert len(fake.site.requests) == before


async def test_br13_a_public_repository_without_an_allowance_is_refused_before_any_write(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.make_public(REPOSITORY)
    fix_id = await proposed_fix(engine, world, now)

    status, check = await draft_check(engine, world, fix_id, now, go=TEST_GO)

    assert (status, check.reason) == ("refused", "public_not_allowed")
    assert writes_seen(fake) == []


async def test_ac26a_the_sweep_returns_a_job_that_never_finished_and_a_late_run_stops(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET state_changed_at = :at WHERE id = :id"),
            {"at": now - timedelta(minutes=21), "id": fix_id},
        )

    await fix_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "did_not_finish")
    before = len(fake.site.requests)
    assert await open_draft(engine, world, fix_id, now) is None
    assert len(fake.site.requests) == before


async def test_ac26_attempts_used_up_go_back_to_proposed_naming_the_failure(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await approved_fix(engine, world, now)
    fake.fail("create_pull", 503)
    token = registry._ATTEMPT.set(3)
    try:
        assert await open_draft(engine, world, fix_id, now) == "proposed"
    finally:
        registry._ATTEMPT.reset(token)

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "github_unavailable")
    # BR8: the branch stored before `POST /pulls` keeps the finding held (AC51).
    assert row.pull_request_branch == fix_branch(fix_id) and row.pull_request_state is None


async def _closed_with_branch(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime, *, state: str
) -> uuid.UUID:
    fix_id = await approved_fix(engine, world, now)
    assert await open_draft(engine, world, fix_id, now) == "opened"
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET pull_request_state = :state WHERE id = :id"),
            {"state": state, "id": fix_id},
        )
    return fix_id


async def _delete(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime) -> Any:
    return await fix_writer.delete_fix_branch(
        world.jobs,
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

    assert set(git_of(fake).refs) == {"main"}
    [deleted] = await audit_rows(engine, world, FIX_BRANCH_DELETED)
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
        git_of(fake).refs[branch] = git_of(fake).refs["main"]
    if why == "no write access":
        fake.grant(REPOSITORY, LOGIN, "read")

    assert await _delete(engine, world, fix_id, now) is None

    assert branch in git_of(fake).refs
    assert not [r for r in fake.site.requests if r.method == "DELETE" and "/git/" in r.path]


async def test_ac62_a_repository_public_by_the_job_gets_no_unconfirmed_note(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """Private at the click, public (and allowed) by the job: GitHub's live answer decides,
    and a note the requester never confirmed for a public body stays out (FR24a)."""
    fix_id = await approved_fix(engine, world, now)
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

    assert await open_draft(engine, world, fix_id, now) == "opened"

    [pull] = fake.pulls_in(REPOSITORY)
    assert "NOTE-MARKER-9" not in pull.body and LOGIN not in pull.body


# ------------------------------------------------------------- Run CI (Step 8)


def _branch_head(fake: FakeGitHub, fix_id: uuid.UUID) -> str:
    return git_of(fake).refs[fix_branch(fix_id)]


def _commit_writes(fake: FakeGitHub, before: int) -> list[str]:
    """Repository writes made after the first `before` requests."""
    return [
        f"{request.method} {request.path}"
        for request in fake.site.requests[before:]
        if request.method in {"POST", "PATCH", "DELETE"} and request.path.startswith("/repos/")
    ]


async def test_with_write_access_off_run_ci_answers_writes_off_with_no_request_to_github(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    await ask_ci(engine, world, fix_id, now)
    head = _branch_head(fake, fix_id)
    before = len(fake.site.requests)

    assert await run_ci(engine, world, fix_id, now, go=None) == "writes_off"

    row = await fix_row(engine, world, fix_id)
    assert (row.last_write_failure, row.ci_commit_sha, row.ci_pushed_at) == (
        "writes_off",
        None,
        None,
    )
    assert len(fake.site.requests) == before
    assert _branch_head(fake, fix_id) == head


async def test_ac28_run_ci_pushes_one_same_tree_commit_with_no_skip_marker(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    git = git_of(fake)
    opening, main = _branch_head(fake, fix_id), git.refs["main"]
    await ask_ci(engine, world, fix_id, now)
    before = len(fake.site.requests)

    assert await run_ci(engine, world, fix_id, now) == "requested"

    commit = git.commits[_branch_head(fake, fix_id)]
    assert commit.parents == (opening,)
    assert commit.tree == git.commits[opening].tree
    assert SKIP_CI_MARKER not in commit.message and SKIP_CHECKS_TRAILER not in commit.message
    assert git.refs["main"] == main and set(git.refs) == {"main", fix_branch(fix_id)}
    branch_path = f"/repos/{REPOSITORY}/git/refs/heads/{fix_branch(fix_id)}"
    assert _commit_writes(fake, before) == [
        f"POST /repos/{REPOSITORY}/git/commits",
        f"PATCH {branch_path}",
    ]
    patched = [r for r in fake.site.requests[before:] if r.method == "PATCH"]
    assert json.loads(patched[0].body) == {"sha": commit.sha, "force": False}
    row = await fix_row(engine, world, fix_id)
    assert (row.ci_commit_sha, row.ci_parent_sha) == (commit.sha, opening)
    assert row.ci_commit_requested_at == now and row.ci_pushed_at == now
    assert row.last_write_failure is None


async def test_ac28_a_crash_after_the_branch_moved_finishes_on_retry_with_no_new_commit(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    await ask_ci(engine, world, fix_id, now)
    fake.drop_after_commit = {"update_ref"}

    with pytest.raises(Retry):
        await run_ci(engine, world, fix_id, now)
    moved = _branch_head(fake, fix_id)
    commits = len(git_of(fake).commits)
    fake.drop_after_commit = set()
    before = len(fake.site.requests)

    assert await run_ci(engine, world, fix_id, now) == "requested"

    assert (
        _branch_head(fake, fix_id) == moved == (await fix_row(engine, world, fix_id)).ci_commit_sha
    )
    assert len(git_of(fake).commits) == commits
    assert _commit_writes(fake, before) == []
    assert (await fix_row(engine, world, fix_id)).ci_pushed_at == now


async def test_ac28_a_crash_before_the_branch_moved_moves_it_to_the_stored_commit(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """The commit is stored before the `PATCH`, so a retry never refuses its own commit."""
    fix_id = await opened_fix(engine, world, now)
    opening = _branch_head(fake, fix_id)
    await ask_ci(engine, world, fix_id, now)
    fake.fail("update_ref", 502)

    with pytest.raises(Retry):
        await run_ci(engine, world, fix_id, now)
    stored = (await fix_row(engine, world, fix_id)).ci_commit_sha
    assert stored is not None and _branch_head(fake, fix_id) == opening
    commits = len(git_of(fake).commits)
    fake.heal("update_ref")

    assert await run_ci(engine, world, fix_id, now) == "requested"

    assert _branch_head(fake, fix_id) == stored
    assert len(git_of(fake).commits) == commits


async def test_ac28_a_second_run_ci_after_a_first_makes_a_second_commit(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    opening = _branch_head(fake, fix_id)
    await ask_ci(engine, world, fix_id, now)
    assert await run_ci(engine, world, fix_id, now) == "requested"
    first = _branch_head(fake, fix_id)
    later = now + timedelta(minutes=3)
    await ask_ci(engine, world, fix_id, later)

    assert await run_ci(engine, world, fix_id, later) == "requested"

    second = git_of(fake).commits[_branch_head(fake, fix_id)]
    assert second.parents == (first,) and first != opening
    assert second.tree == git_of(fake).commits[opening].tree
    row = await fix_row(engine, world, fix_id)
    assert (row.ci_commit_sha, row.ci_parent_sha) == (second.sha, first)
    # The same request again does nothing more.
    assert await run_ci(engine, world, fix_id, later) is None


async def test_ac28_a_head_that_is_not_nexovue_s_is_refused_with_nothing_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    pushed = fake.seed_files(REPOSITORY, {OTHER: "x = 3\n"}, branch=fix_branch(fix_id))
    await ask_ci(engine, world, fix_id, now)
    before = len(fake.site.requests)

    assert await run_ci(engine, world, fix_id, now) == "branch_changed"

    assert _branch_head(fake, fix_id) == pushed
    assert _commit_writes(fake, before) == []
    row = await fix_row(engine, world, fix_id)
    assert (row.last_write_failure, row.ci_commit_sha) == ("branch_changed", None)


@pytest.mark.parametrize(
    ("arrange", "failure"),
    [
        ("read_only_person", "no_github_access"),
        ("drafts_withdrawn", "drafts_not_allowed"),
        ("closed", "pull_request_not_open"),
    ],
)
async def test_ac28_run_ci_is_refused_without_access_or_an_open_draft(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    arrange: str,
    failure: str,
) -> None:
    fix_id = await opened_fix(engine, world, now)
    head = _branch_head(fake, fix_id)
    await ask_ci(engine, world, fix_id, now)
    if arrange == "read_only_person":
        fake.grant(REPOSITORY, LOGIN, "read")
    statement = {
        "drafts_withdrawn": "UPDATE fix_repository_settings SET drafts_allowed = false "
        "WHERE org_id = :org",
        "closed": "UPDATE finding_fixes SET pull_request_state = 'closed' WHERE org_id = :org",
    }.get(arrange)
    if statement is not None:
        async with engine.begin() as connection:
            await connection.execute(text(statement), {"org": world.org_id})
    before = len(fake.site.requests)

    assert await run_ci(engine, world, fix_id, now) == failure

    if arrange == "closed":
        # A draft the poller found ended is refused from the row, asking GitHub nothing.
        assert len(fake.site.requests) == before
    assert _commit_writes(fake, before) == []
    assert _branch_head(fake, fix_id) == head
    assert (await fix_row(engine, world, fix_id)).last_write_failure == failure


async def test_run_ci_never_writes_a_ref_but_the_suggestion_s_own(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """FR23, AC7: `move_branch` takes the suggestion's id and builds the ref itself."""
    fix_id = await opened_fix(engine, world, now)
    await ask_ci(engine, world, fix_id, now)

    assert await run_ci(engine, world, fix_id, now) == "requested"

    patched = {r.path for r in fake.site.requests if r.method == "PATCH"}
    assert patched == {f"/repos/{REPOSITORY}/git/refs/heads/{fix_branch(fix_id)}"}
