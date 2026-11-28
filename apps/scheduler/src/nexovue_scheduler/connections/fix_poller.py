"""`connections.poll_fix_pull_requests`: F5's own poller, which keeps a draft's stored state
true to GitHub (F5 design, *Jobs*, *The draft click*'s exits; spec FR27, FR30, BR8, AC28b,
AC49, AC51; plan T8.2). F22's sync loop does not follow pull requests.

Beat adds it to `connections` every 5 minutes while connections are on. It **reads only**:
two read routes of the Fix App (`GET /repos/{r}/pulls/{n}`, and `?head=` for a branch with no
pull request stored), through a `pull_requests: read` token minted per repository and revoked
at the end. It never builds `FixWrites`, so it needs no go: write access being off in code
(ADR-097) stops what it could find, since no row holds a branch until a draft job has written
one (status, open decision 104). Each pass, across every organization, the least recently
visited rows first, each written by its own `org_id` and only while it still reads as the
poller read it:

1. **An open draft:** GitHub's state is stored: `merged` with GitHub's merge time and the
   merge's commit (FR30), or `closed` with its close time, which frees the finding (BR8,
   AC28b). **A 404 on a token that reaches the repository is `untracked`** (AC28b, AC49).
2. **A branch held with no pull request stored** (a lost answer, attempts used up, or the
   sweep's `did_not_finish`; AC51): a pull request on `nexovue/suggestion-<id>` whose head is
   the commit Nexovue stored is adopted, `opened`, audited and on the timeline as the draft
   job would have; one with another head is `untracked` (Nexovue cannot say it is its own);
   none is `not_opened`, naming the leftover branch. Each frees the finding.

A repository the App can no longer reach is skipped: the sweep marks its pull requests
`untracked` once access has been gone 72 hours (`fix_sweep.py`, step 6). Logs carry counts
and codes only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.cases import (
    RECORD_CASE_AUDIT_SQL,
    RECORD_FINDING_EVENT_SQL,
    case_audit_parameters,
    finding_event_parameters,
)
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.records import (
    FIX_PULL_REQUEST_OPENED,
    FIX_PULL_REQUEST_OPENED_EVENT,
    POLL_FIX_PULL_REQUESTS_TASK,
    PULL_REQUEST_CLOSED,
    PULL_REQUEST_MERGED,
    PULL_REQUEST_NOT_OPENED,
    PULL_REQUEST_OPEN,
    PULL_REQUEST_UNTRACKED,
    fix_branch,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections.fix_github import (
    FixEndpoints,
    FixGitHub,
    GitHubFailure,
    PullFacts,
)
from nexovue_scheduler.connections.source_pack import Ending, endpoints, fix_client, load_fix_row
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# Rows visited per pass: a few hundred GitHub reads in the poller's 60 seconds (a guess).
BATCH: Final = 100
_GITHUB_STATE: Final = {"open": PULL_REQUEST_OPEN, "closed": PULL_REQUEST_CLOSED}

# Across every organization: drafts Nexovue still follows (BR8's held rows), least recently
# visited first. Each carries its `org_id`; every write below binds it.
_FOLLOWED: Final = text(
    """
    SELECT fix.id, fix.org_id, fix.finding_id, fix.connection_repository_id,
           fix.pull_request_state, fix.pull_request_number, fix.pull_request_commit_sha,
           fix.edits
    FROM finding_fixes AS fix
    JOIN connection_repositories AS place
      ON place.org_id = fix.org_id AND place.id = fix.connection_repository_id
    JOIN connections AS app ON app.org_id = place.org_id AND app.id = place.connection_id
    WHERE fix.kind = 'source' AND fix.pull_request_branch IS NOT NULL
      AND fix.state <> 'approved'
      AND (fix.pull_request_state IS NULL OR fix.pull_request_state = 'open')
      AND app.kind = 'github_fix' AND app.state <> 'disconnected'
      AND (CAST(:only_org AS uuid) IS NULL OR fix.org_id = CAST(:only_org AS uuid))
    ORDER BY fix.pull_request_polled_at NULLS FIRST, fix.id
    LIMIT :batch
    """
)
_VISITED: Final = text(
    "UPDATE finding_fixes SET pull_request_polled_at = :at WHERE org_id = :org_id AND id = :fix_id"
)
# An open draft's new state, only while it is still the open one the poller read.
_FOLLOWED_TO: Final = text(
    """
    UPDATE finding_fixes
    SET pull_request_state = :state, pull_request_state_at = :state_at,
        pull_request_merge_commit_sha = :merge_commit, pull_request_polled_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND pull_request_state = 'open'
      AND pull_request_number = :number
    RETURNING id
    """
)
# A held branch's pull request, found: adopted as the draft job would have stored it.
_ADOPTED: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'opened', state_changed_at = :at, pull_request_state = :state,
        pull_request_state_at = :state_at, pull_request_number = :number,
        pull_request_url = :url, pull_request_merge_commit_sha = :merge_commit,
        last_write_failure = NULL, pull_request_polled_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state <> 'approved'
      AND pull_request_branch IS NOT NULL AND pull_request_state IS NULL
    RETURNING finding_id, approved_by
    """
)
# A held branch settled with no pull request of Nexovue's: `not_opened` or `untracked`.
_HELD_TO: Final = text(
    """
    UPDATE finding_fixes
    SET pull_request_state = :state, pull_request_state_at = :at, pull_request_polled_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state <> 'approved'
      AND pull_request_branch IS NOT NULL AND pull_request_state IS NULL
    RETURNING id
    """
)


@dataclass(slots=True)
class PolledPullRequests:
    visited: int = 0
    changed: int = 0
    adopted: int = 0
    settled: int = 0
    unreachable: int = 0


@dataclass(slots=True)
class _Reader:
    """One repository's Fix App and read token for this pass, revoked at its end."""

    github: FixGitHub
    token: str
    key: str


@dataclass(slots=True)
class _Pass:
    engine: AsyncEngine
    keys: ConfiguredKeys
    endpoints: FixEndpoints
    now: datetime
    counts: PolledPullRequests = field(default_factory=PolledPullRequests)
    readers: dict[Any, _Reader | None] = field(default_factory=dict)


async def poll(
    engine: AsyncEngine,
    *,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    clock: Callable[[], datetime],
    only_org: str | None = None,
) -> PolledPullRequests:
    """One pass. `only_org` narrows it to one organization, for a test, so a test's poll
    never reaches another test's rows."""
    run = _Pass(engine=engine, keys=keys, endpoints=endpoints, now=clock())
    async with engine.connect() as connection:
        rows = (await connection.execute(_FOLLOWED, {"batch": BATCH, "only_org": only_org})).all()
    try:
        for row in rows:
            await _visit(run, row)
    finally:
        for reader in run.readers.values():
            if reader is not None:
                reader.github.revoke(reader.token)
    return run.counts


async def _visit(run: _Pass, row: Any) -> None:
    run.counts.visited += 1
    ids = {"org_id": row.org_id, "fix_id": row.id}
    reader = await _reader(run, row)
    if reader is None:
        run.counts.unreachable += 1
        await _touch(run, ids)
        return
    try:
        if row.pull_request_state == PULL_REQUEST_OPEN and row.pull_request_number is not None:
            await _follow_open(run, row, reader, ids)
        else:
            await _settle_held(run, row, reader, ids)
    except GitHubFailure as failure:
        logger.info("a pull request could not be read", fix_id=str(row.id), failure=failure.code)
        run.counts.unreachable += 1
        await _touch(run, ids)


async def _reader(run: _Pass, row: Any) -> _Reader | None:
    """The repository's reader, minted once per pass; None when the App cannot reach it."""
    place_id = row.connection_repository_id
    if place_id in run.readers:
        return run.readers[place_id]
    found: _Reader | None = None
    place = await load_fix_row(run.engine, str(row.org_id), str(row.id))
    if place is not None:
        try:
            github = fix_client(place, run.keys, run.endpoints)
            token = github.pull_request_read_token(
                place.github_installation_id, int(place.external_id)
            )
            found = _Reader(github=github, token=token, key=place.key)
        except (Ending, GitHubFailure) as unreachable:
            logger.info("a repository's pull requests could not be read", failure=str(unreachable))
    run.readers[place_id] = found
    return found


async def _follow_open(run: _Pass, row: Any, reader: _Reader, ids: dict[str, Any]) -> None:
    """Step 1: an open draft's state, or `untracked` on GitHub's 404."""
    try:
        pull = reader.github.pull(reader.token, reader.key, row.pull_request_number)
    except GitHubFailure as failure:
        if failure.code != "not_found":
            raise
        await _store_open(run, row, ids, PULL_REQUEST_UNTRACKED, state_at=run.now)
        return
    state = _state_of(pull)
    if state == PULL_REQUEST_OPEN:
        await _touch(run, ids)
        return
    await _store_open(
        run,
        row,
        ids,
        state,
        state_at=pull.ended_at or run.now,
        merge_commit=pull.merge_commit_sha,
    )


async def _store_open(
    run: _Pass,
    row: Any,
    ids: dict[str, Any],
    state: str,
    *,
    state_at: datetime,
    merge_commit: str | None = None,
) -> None:
    async with run.engine.begin() as connection:
        moved = (
            await connection.execute(
                _FOLLOWED_TO,
                {
                    **ids,
                    "state": state,
                    "state_at": state_at,
                    "merge_commit": merge_commit,
                    "number": row.pull_request_number,
                    "at": run.now,
                },
            )
        ).one_or_none()
    if moved is not None:
        run.counts.changed += 1
        logger.info("a pull request's state moved", fix_id=str(row.id), state=state)


async def _settle_held(run: _Pass, row: Any, reader: _Reader, ids: dict[str, Any]) -> None:
    """Step 2 (AC51): adopt Nexovue's pull request by `?head=`, or settle the hold."""
    pulls = reader.github.pulls_for(reader.token, reader.key, str(row.id))
    ours = next((each for each in pulls if each.head_sha == row.pull_request_commit_sha), None)
    if ours is not None:
        await _adopt(run, row, ids, ours)
        return
    settled = PULL_REQUEST_UNTRACKED if pulls else PULL_REQUEST_NOT_OPENED
    async with run.engine.begin() as connection:
        moved = (
            await connection.execute(_HELD_TO, {**ids, "state": settled, "at": run.now})
        ).one_or_none()
    if moved is not None:
        run.counts.settled += 1
        logger.info("a held branch was settled", fix_id=str(row.id), state=settled)


async def _adopt(run: _Pass, row: Any, ids: dict[str, Any], pull: PullFacts) -> None:
    state = _state_of(pull)
    async with run.engine.begin() as connection:
        adopted = (
            await connection.execute(
                _ADOPTED,
                {
                    **ids,
                    "at": run.now,
                    "state": state,
                    "state_at": run.now if state == PULL_REQUEST_OPEN else pull.ended_at or run.now,
                    "number": pull.number,
                    "url": pull.url,
                    "merge_commit": pull.merge_commit_sha,
                },
            )
        ).one_or_none()
        if adopted is None:
            return
        await connection.execute(
            text(RECORD_CASE_AUDIT_SQL),
            case_audit_parameters(
                org_id=row.org_id,
                actor_user_id=adopted.approved_by,
                action=FIX_PULL_REQUEST_OPENED,
                target=f"{FINDING_AUDIT_TARGET}:{adopted.finding_id}",
                detail={
                    "fix_id": str(row.id),
                    "pull_request_number": pull.number,
                    "branch": fix_branch(row.id),
                    "adopted": True,
                },
            ),
        )
        await connection.execute(
            text(RECORD_FINDING_EVENT_SQL),
            finding_event_parameters(
                FIX_PULL_REQUEST_OPENED_EVENT,
                org_id=row.org_id,
                finding_id=adopted.finding_id,
                actor_kind="system",
                at=run.now,
                fix_id=row.id,
                step_count=len({each.get("file_ref") for each in row.edits or []}),
            ),
        )
    run.counts.adopted += 1
    logger.info("a pull request was adopted", fix_id=str(row.id), number=pull.number)


def _state_of(pull: PullFacts) -> str:
    if pull.merged:
        return PULL_REQUEST_MERGED
    state = _GITHUB_STATE.get(pull.state)
    if state is None:
        raise GitHubFailure("unexpected")
    return state


async def _touch(run: _Pass, ids: dict[str, Any]) -> None:
    async with run.engine.begin() as connection:
        await connection.execute(_VISITED, {**ids, "at": run.now})


@task(POLL_FIX_PULL_REQUESTS_TASK)
def poll_fix_pull_requests_task() -> None:
    settings = get_settings()
    polled = with_engine(
        lambda engine: poll(
            engine,
            keys=settings.connection_secret_keys,
            endpoints=endpoints(),
            clock=lambda: datetime.now(UTC),
        )
    )
    if polled.changed or polled.adopted or polled.settled or polled.unreachable:
        logger.info(
            "the pull request poller moved rows",
            visited=polled.visited,
            changed=polled.changed,
            adopted=polled.adopted,
            settled=polled.settled,
            unreachable=polled.unreachable,
        )
