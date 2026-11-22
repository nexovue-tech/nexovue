"""F5's own pull request poller, and the sweep's Step 8 passes, against the simulated GitHub
(F5 plan T8.2; spec FR27, FR30, BR8, BR9, AC28b, AC49, AC51).

The poller **only reads**: every request it makes is a read, or a `pull_requests: read` token
minted for one repository. While write access is off in code (ADR-097) no row holds a branch,
so it finds nothing to follow; the drafts it follows here were opened by the draft job with a
go built only in these tests.

Each test builds and drops its own organization and polls or sweeps only that organization
(`only_org`), so a test never reaches another test's rows; the clock is the test's own.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Final

import fix_writer_fixtures as fixtures
import pytest
from alert_fixtures import postgres_reachable
from fix_writer_fixtures import (
    approved_fix,
    audit_rows,
    open_draft,
    opened_fix,
    proposed_fix,
    repository_writes,
)
from live import require_stack
from nexovue_platform.fixes.records import FIX_PULL_REQUEST_OPENED, fix_branch
from source_fix_fixtures import KEYS, REPOSITORY, SourceWorld, fix_row
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub

from nexovue_scheduler.connections import fix_poller, fix_sweep
from nexovue_scheduler.jobs.registry import Retry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_pull_request_state.py",
    remedy="Postgres is not reachable — run `just up`",
)

now = fixtures.now
fake = fixtures.fake
world = fixtures.world

MERGED_AT: Final = "2026-11-22T10:00:00+00:00"
CLOSED_AT: Final = "2026-11-22T09:30:00+00:00"
_READS: Final = frozenset({"GET"})


async def _poll(engine: AsyncEngine, world: SourceWorld, now: datetime) -> Any:
    return await fix_poller.poll(
        world.jobs,
        keys=KEYS,
        endpoints=world.endpoints(),
        clock=lambda: now,
        only_org=str(world.org_id),
    )


async def _number(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID) -> int:
    number = (await fix_row(engine, world, fix_id)).pull_request_number
    assert isinstance(number, int)
    return number


async def _second_draft_claims(engine: AsyncEngine, world: SourceWorld, now: datetime) -> bool:
    """Whether another suggestion on the same finding may now be claimed for a draft (BR8):
    the one-draft index refuses it while the first still holds the finding."""
    second = await proposed_fix(engine, world, now)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE finding_fixes SET state = 'approved', approved_by = :user, "
                    "approved_at = :now, approved_digest = :digest WHERE id = :id"
                ),
                {"user": world.user_id, "now": now, "digest": "0" * 64, "id": second},
            )
    except IntegrityError as refused:
        assert "uq_finding_fixes_one_draft" in str(refused.orig)
        return False
    return True


def _poller_requests(fake: FakeGitHub, before: int) -> list[Any]:
    return list(fake.site.requests[before:])


async def _held_branch(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> uuid.UUID:
    """AC51: the branch made and stored, `POST /pulls` applied and its answer lost, then the
    job's attempts used up (the sweep's `did_not_finish` return)."""
    fix_id = await approved_fix(engine, world, now)
    fake.drop_after_commit = {"create_pull"}
    with pytest.raises(Retry):
        await open_draft(engine, world, fix_id, now)
    fake.drop_after_commit = set()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET state = 'proposed', "
                "last_write_failure = 'did_not_finish' WHERE id = :id"
            ),
            {"id": fix_id},
        )
    row = await fix_row(engine, world, fix_id)
    assert row.pull_request_branch == fix_branch(fix_id) and row.pull_request_state is None
    return fix_id


# ------------------------------------------------------------------- reads only


async def test_with_no_draft_the_poller_reaches_nothing(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """As shipped no row holds a branch, so there is nothing to follow and nothing is asked."""
    await proposed_fix(engine, world, now)
    before = len(fake.site.requests)

    polled = await _poll(engine, world, now)

    assert polled.visited == 0
    assert _poller_requests(fake, before) == []


async def test_the_poller_only_reads(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    fake.close_pull(REPOSITORY, await _number(engine, world, fix_id), at=CLOSED_AT)
    writes_before = repository_writes(fake)
    before = len(fake.site.requests)

    await _poll(engine, world, now)

    made = _poller_requests(fake, before)
    assert made, "the poller read the pull request"
    for request in made:
        if request.path.endswith("/access_tokens"):
            asked = json.loads(request.body)
            assert set(asked["permissions"].values()) == {"read"}, asked
        elif request.method != "DELETE" or request.path != "/installation/token":
            assert request.method in _READS, f"{request.method} {request.path}"
    assert repository_writes(fake) == writes_before


# ------------------------------------------------------ an open draft (AC28b)


async def test_ac28b_a_draft_closed_on_github_frees_the_finding_within_one_poll(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    assert await _second_draft_claims(engine, world, now) is False
    fake.close_pull(REPOSITORY, await _number(engine, world, fix_id), at=CLOSED_AT)

    polled = await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert (polled.changed, row.state, row.pull_request_state) == (1, "opened", "closed")
    assert row.pull_request_state_at == datetime.fromisoformat(CLOSED_AT)
    assert row.pull_request_polled_at == now
    assert await _second_draft_claims(engine, world, now) is True


async def test_fr30_a_merged_draft_stores_github_s_merge_time_and_commit(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    merged = fake.merge_pull(REPOSITORY, await _number(engine, world, fix_id), at=MERGED_AT)

    await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert row.pull_request_state == "merged"
    assert row.pull_request_state_at == datetime.fromisoformat(MERGED_AT)
    assert row.pull_request_merge_commit_sha == merged


async def test_ac28b_a_404_reads_untracked_and_frees_the_finding(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    fake.delete_pull(REPOSITORY, await _number(engine, world, fix_id))

    await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert (row.pull_request_state, row.pull_request_state_at) == ("untracked", now)
    assert await _second_draft_claims(engine, world, now) is True


async def test_an_open_draft_stays_open_and_is_marked_visited(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    later = now + timedelta(minutes=5)

    polled = await _poll(engine, world, later)

    row = await fix_row(engine, world, fix_id)
    assert (polled.visited, polled.changed) == (1, 0)
    assert (row.pull_request_state, row.pull_request_polled_at) == ("open", later)


async def test_a_repository_the_app_cannot_reach_is_skipped_not_untracked(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """Lost access is the sweep's to settle after 72 hours, never one poll's."""
    fix_id = await opened_fix(engine, world, now)
    fake.remove_repository(REPOSITORY)

    polled = await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert (polled.unreachable, row.pull_request_state) == (1, "open")


# ---------------------------------------------------------- a held branch (AC51)


async def test_ac51_a_held_branch_holds_the_finding_and_is_adopted_by_head(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _held_branch(engine, world, fake, now)
    assert await _second_draft_claims(engine, world, now) is False
    [made] = fake.pulls_in(REPOSITORY)

    polled = await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert polled.adopted == 1
    assert (row.state, row.pull_request_state, row.pull_request_number) == (
        "opened",
        "open",
        made.number,
    )
    assert row.last_write_failure is None
    [entry] = await audit_rows(engine, world, FIX_PULL_REQUEST_OPENED)
    assert entry.actor_user_id == world.user_id and entry.detail["adopted"] is True
    assert len(fake.pulls_in(REPOSITORY)) == 1
    # A late retry of the draft job finds the state moved and makes no second pull request.
    assert await open_draft(engine, world, fix_id, now) is None
    assert len(fake.pulls_in(REPOSITORY)) == 1


async def test_ac51_with_no_pull_request_found_the_hold_clears_as_not_opened(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _held_branch(engine, world, fake, now)
    fake.pulls.clear()

    polled = await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert polled.settled == 1
    assert (row.state, row.pull_request_state) == ("proposed", "not_opened")
    assert row.pull_request_branch == fix_branch(fix_id)
    assert await _second_draft_claims(engine, world, now) is True


async def test_a_pull_request_on_the_branch_with_another_head_reads_untracked(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _held_branch(engine, world, fake, now)
    [made] = fake.pulls_in(REPOSITORY)
    made.head_sha = fake.seed_files(REPOSITORY, {"other.py": "y = 1\n"}, branch=fix_branch(fix_id))

    await _poll(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.pull_request_state) == ("proposed", "untracked")


async def test_an_approved_row_is_left_to_its_job(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _held_branch(engine, world, fake, now)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_fixes SET state = 'approved' WHERE id = :id"), {"id": fix_id}
        )

    polled = await _poll(engine, world, now)

    assert polled.visited == 0
    assert (await fix_row(engine, world, fix_id)).pull_request_state is None


# --------------------------------------------------------------- the sweep (Step 8)


async def _sweep(engine: AsyncEngine, world: SourceWorld, now: datetime) -> Any:
    return await fix_sweep.sweep(world.jobs, clock=lambda: now, only_org=str(world.org_id))


async def test_ac49_access_gone_72_hours_marks_a_followed_pull_request_untracked(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connection_repositories SET is_reachable = false, "
                "unreachable_since = :since WHERE id = :id"
            ),
            {"since": now - timedelta(hours=73), "id": world.repository_id},
        )

    swept = await _sweep(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    assert swept.untracked == 1
    assert (row.pull_request_state, row.pull_request_state_at) == ("untracked", now)
    assert await _second_draft_claims(engine, world, now) is True


async def test_access_gone_under_72_hours_leaves_it_followed(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await opened_fix(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE connection_repositories SET is_reachable = false, "
                "unreachable_since = :since WHERE id = :id"
            ),
            {"since": now - timedelta(hours=71), "id": world.repository_id},
        )

    await _sweep(engine, world, now)

    assert (await fix_row(engine, world, fix_id)).pull_request_state == "open"


@pytest.mark.parametrize(("days", "deleted"), [(8, True), (6, False)])
async def test_br9_code_goes_7_days_after_the_pull_request_ended(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    days: int,
    deleted: bool,
) -> None:
    fix_id = await opened_fix(engine, world, now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET pull_request_state = 'merged', "
                "pull_request_state_at = :at WHERE id = :id"
            ),
            {"at": now - timedelta(days=days), "id": fix_id},
        )

    await _sweep(engine, world, now)

    row = await fix_row(engine, world, fix_id)
    if deleted:
        assert (row.code_deleted_reason, row.edits, row.files_read) == (
            "pull_request_done",
            None,
            None,
        )
        assert row.pull_request_number is not None and row.pull_request_state == "merged"
    else:
        assert row.code_deleted_at is None and row.edits is not None
