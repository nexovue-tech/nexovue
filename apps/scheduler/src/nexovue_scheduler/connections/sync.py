"""Staying in step with a tracker by polling (F22 plan T4.5; design, *The job*,
*The comparison*, *Creating without duplicates*).

`sweep_connections`, from beat every 30 seconds while a tracker connection is live, adds
one `sync_connection` job per connection with a due link, by a conditional insert, and
each connection's hourly repository refresh. `sync_connection` then, for one connection:

0. binds the job's own `org_id` and the connection's id on every read: a job naming
   another organization's connection finds no row and stops (AC129);
1. takes the connection's lease by compare-and-set, so at most one run per connection
   (AC48); a held lease stops it;
2. stops while the connection is paused (`paused_until`, after a failure or a limit);
3. takes up to 50 due links, oldest first;
4. creates an issue for a link `creating`, or reads, compares and acts for the others;
5. before each call out, reads the connection's state with no lock, and stops if it is
   no longer usable, so a disconnect stops a running job before its next write (AC37);
6. commits per link, and keeps GitHub writes at least a second apart (NFR4);
7. ends the run at the first timeout, connection failure or 5xx, pausing the
   connection with backoff, or for as long as a rate limit asked (AC49, AC86);
8. revokes the run's tokens.

**Kind-agnostic** (foundation item 29): nothing here names GitHub. The adapter for the
connection's kind does the calls (`trackers.Tracker`); Jira later is a second adapter.

**No lock is held across a call out.** Each link is read unlocked, the call is made,
and then one transaction locks the case's finding `FOR NO KEY UPDATE`, then the link,
reads both again, compares again, and applies what it finds. A crash anywhere leaves
the next run on row 2 or 3 of the table, or on a creation retry, which looks for the
half-made issue before creating (AC44). A comment is committed as written before it is
posted, so a crash leaves zero comments and a retry cannot post two (AC87).

**Fault points** (`fault`): the harness kills a worker at each to prove the above. In
production the hook does nothing.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.cases import (
    CASE_IDLE_LIMIT,
    CLEAR_HOLD_SQL,
    JOIN_OPEN_SQL,
    RECORD_EMAIL_OWED_SQL,
    REOPEN_CASE_SQL,
    clear_hold_parameters,
    email_owed_parameters,
    join_open_parameters,
    reopen_case_parameters,
)
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.connections import (
    ACTIVE_POLL,
    FAILING,
    FOLLOW_AFTER_RESOLVE,
    GONE_RECHECK,
    GONE_RECHECK_FOR,
    IDLE_WARNING,
    REPOSITORY_REFRESH,
    RESOLVED_POLL,
    SYNC_HARD_LIMIT_SECONDS,
    TRACKER_KINDS,
    WORKING,
    CommentText,
    IssueFacts,
    close_reason_for,
    comment_text,
    github_issue_key,
    issue_text,
    refresh_job,
    sync_job,
)
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.case_endings import (
    CASE_REOPENED_ACTION,
    end_case,
    record_audit,
    record_event,
)
from nexovue_scheduler.connections import states
from nexovue_scheduler.connections.compare import (
    CLOSED,
    INVESTIGATING,
    OPEN,
    RESOLVED,
    Action,
    CaseView,
    LinkView,
    compare,
    compare_read,
)
from nexovue_scheduler.connections.store import (
    Loaded,
    load_connection,
    stored_connection,
    write_standing,
)
from nexovue_scheduler.connections.trackers import (
    IssueMoved,
    IssueRef,
    IssueSeen,
    PlaceKey,
    StoredConnection,
    Tracker,
    TrackerFailure,
)

logger = get_logger(__name__)

# How many due links one run takes; the rest stay due for the next (design).
LINKS_PER_RUN: Final = 50
# A run's lease outlives its hard limit, so a killed run's lease lapses on its own.
LEASE_SECONDS: Final = SYNC_HARD_LIMIT_SECONDS + 30
# GitHub writes at least this far apart (NFR4, AC49).
WRITE_SPACING_SECONDS: Final = 1.0
# Backoff after a failure that may pass: 1, 2, 4 … minutes, to at most an hour, with
# some randomness so connections that failed together do not retry together (design).
BACKOFF_START: Final = timedelta(minutes=1)
BACKOFF_CEILING: Final = timedelta(hours=1)
BACKOFF_JITTER: Final = 0.2
# Failures that may pass: they end the run and pause the connection (AC86).
_PASSING: Final = ("unreachable", "timed_out", "server_error")
# What ends a run: those, a rate limit (AC49), and a credential GitHub refuses, which the
# connection's state machine turns into `needs_attention`.
_ENDS_THE_RUN: Final = (*_PASSING, "rate_limited", "unauthorized")
# Connection states a run may call out in. `failing` keeps trying, which is how it
# recovers; `needs_attention` waits for a person (design, *Failure handling*).
_USABLE: Final = (WORKING, FAILING)

FaultPoint = Callable[[str], None]


def _no_fault(_: str) -> None:
    """Production's fault hook: the harness replaces it in a child it kills."""


# The harness sets this in its forked worker; nothing else does (design, *Test harness*).
fault: FaultPoint = _no_fault


class _RunEnds(Exception):  # noqa: N818 — a signal, not an error
    """Stop this run: the connection is gone, paused or failing."""


# ------------------------------------------------------------------- statements
# Every one binds the job's own `org_id` (AC129).

_LOAD: Final = text(
    """
    SELECT id, org_id, kind, state, sealed_credential, github_app_id, github_app_slug,
           github_installation_id, paused_until, consecutive_failures, failing_since,
           last_failure, last_failure_status
    FROM connections
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
_TAKE_LEASE: Final = text(
    """
    UPDATE connections SET sync_lease_until = :until
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
      AND (sync_lease_until IS NULL OR sync_lease_until < :now)
    RETURNING sync_lease_until
    """
)
_RELEASE_LEASE: Final = text(
    """
    UPDATE connections SET sync_lease_until = NULL
    WHERE org_id = :org_id AND id = :connection_id AND sync_lease_until = :until
    """
)
_PAUSE: Final = text(
    """
    UPDATE connections SET paused_until = :until
    WHERE org_id = :org_id AND id = :connection_id AND state <> 'disconnected'
    """
)
_STATE: Final = text("SELECT state FROM connections WHERE org_id = :org_id AND id = :connection_id")
_DUE_LINKS: Final = text(
    """
    SELECT link.id, link.finding_id
    FROM finding_links AS link
    WHERE link.org_id = :org_id AND link.connection_id = :connection_id
      AND link.next_sync_at <= :now
      AND link.sync_status NOT IN ('stopped', 'refused')
    ORDER BY link.next_sync_at
    LIMIT :limit
    """
)
# A link, its place, and its case as the comparison needs it.
_LINK_COLUMNS: Final = """
    link.id, link.finding_id, link.role, link.sync_status, link.external_id,
    link.external_key, link.seen_state, link.wanted_state, link.comment_wanted,
    link.idle_warning_at, link.tracker_closed_at, link.frozen_facts, link.first_attempt_at,
    link.gone_at, link.failures, link.last_failure, link.place_id,
    place.external_id AS place_external_id, place.key AS place_key,
    place.is_private, place.public_allowed_at,
    root.case_state, root.case_resolved_at, root.case_activity_at,
    root.project_id, root.scope, root.service_name,
    (root.state = 'open' OR EXISTS (
        SELECT 1 FROM findings AS joined
        WHERE joined.org_id = :org_id AND joined.root_finding_id = root.id
          AND joined.state = 'open')) AS signal_open
"""
_READ_LINK: Final = text(
    f"""
    SELECT {_LINK_COLUMNS}
    FROM finding_links AS link
    JOIN findings AS root ON root.org_id = link.org_id AND root.id = link.finding_id
    LEFT JOIN connection_repositories AS place
      ON place.org_id = link.org_id AND place.id = link.place_id
    WHERE link.org_id = :org_id AND link.id = :link_id AND link.connection_id = :connection_id
    """
)
_LOCK_ROOT: Final = text(
    """
    SELECT id FROM findings WHERE org_id = :org_id AND id = :finding_id
    FOR NO KEY UPDATE
    """
)
_LOCK_LINK: Final = text(
    f"""
    SELECT {_LINK_COLUMNS}
    FROM finding_links AS link
    JOIN findings AS root ON root.org_id = link.org_id AND root.id = link.finding_id
    LEFT JOIN connection_repositories AS place
      ON place.org_id = link.org_id AND place.id = link.place_id
    WHERE link.org_id = :org_id AND link.id = :link_id AND link.connection_id = :connection_id
    FOR UPDATE OF link
    """
)
_FIRST_ATTEMPT: Final = text(
    """
    UPDATE finding_links SET first_attempt_at = :at
    WHERE org_id = :org_id AND id = :link_id AND sync_status = 'creating'
      AND first_attempt_at IS NULL
    """
)
_CREATED: Final = text(
    """
    UPDATE finding_links
    SET sync_status = 'in_sync', external_id = :external_id, external_key = :external_key,
        seen_state = 'open', frozen_facts = NULL, failures = 0, last_failure = NULL,
        last_synced_at = :at, next_sync_at = :next_sync_at
    WHERE org_id = :org_id AND id = :link_id AND sync_status = 'creating'
    """
)
_REFUSED: Final = text(
    """
    UPDATE finding_links
    SET sync_status = 'refused', frozen_facts = NULL, last_failure = :failure,
        wanted_state = NULL, comment_wanted = NULL, next_sync_at = NULL
    WHERE org_id = :org_id AND id = :link_id AND sync_status = 'creating'
    """
)
_SEEN: Final = text(
    """
    UPDATE finding_links
    SET seen_state = :seen, wanted_state = :wanted, tracker_closed_at = :tracker_closed_at,
        sync_status = :status, failures = 0, last_failure = :failure, gone_at = NULL,
        last_synced_at = :at, next_sync_at = :next_sync_at
    WHERE org_id = :org_id AND id = :link_id
    """
)
_SET_STATUS: Final = text(
    """
    UPDATE finding_links
    SET sync_status = :status, last_failure = :failure, gone_at = :gone_at,
        next_sync_at = :next_sync_at, last_synced_at = COALESCE(:synced_at, last_synced_at)
    WHERE org_id = :org_id AND id = :link_id
    """
)
_WAITING: Final = text(
    """
    UPDATE finding_links
    SET sync_status = CASE WHEN sync_status IN ('creating', 'promoting')
                           THEN sync_status ELSE 'waiting' END,
        failures = failures + 1, last_failure = :failure, next_sync_at = :next_sync_at
    WHERE org_id = :org_id AND id = :link_id
    """
)
_MOVE: Final = text(
    """
    UPDATE finding_links
    SET external_id = :external_id, external_key = :external_key, place_id = :place_id,
        next_sync_at = :at
    WHERE org_id = :org_id AND id = :link_id
    """
)
_PLACE_BY_KEY: Final = text(
    """
    SELECT id FROM connection_repositories
    WHERE org_id = :org_id AND connection_id = :connection_id AND key = :key AND is_reachable
    """
)
_UNREACHABLE_PLACE: Final = text(
    """
    UPDATE connection_repositories SET is_reachable = false
    WHERE org_id = :org_id AND id = :place_id
    """
)
# The comment owed is marked written before it is posted (AC87): only one run can take it.
_TAKE_COMMENT: Final = text(
    """
    UPDATE finding_links
    SET comment_wanted = NULL, comment_written_at = :at, next_sync_at = :next_sync_at
    WHERE org_id = :org_id AND id = :link_id AND comment_wanted = :kind
      AND wanted_state IS NULL
    RETURNING id
    """
)
_SETTLING: Final = text(
    """
    UPDATE finding_links SET next_sync_at = :next_sync_at
    WHERE org_id = :org_id AND id = :link_id
    """
)

# The sweep's first read crosses organizations by nature, and is a named exception in
# the tenancy test; each job it adds carries the row's own `org_id`.
_CONNECTIONS_DUE: Final = text(
    """
    SELECT connection.org_id, connection.id,
           connection.state IN ('working', 'failing') AND EXISTS (
               SELECT 1 FROM finding_links AS link
               WHERE link.org_id = connection.org_id AND link.connection_id = connection.id
                 AND link.next_sync_at <= :now
                 AND link.sync_status NOT IN ('stopped', 'refused')) AS sync_due,
           (connection.repositories_read_at IS NULL
            OR connection.repositories_read_at < :refresh_before) AS refresh_due
    FROM connections AS connection
    WHERE connection.state IN ('working', 'failing', 'needs_attention')
      AND connection.kind = ANY(CAST(:kinds AS text[]))
      AND connection.github_installation_id IS NOT NULL
      AND connection.sealed_credential IS NOT NULL
      AND (connection.paused_until IS NULL OR connection.paused_until <= :now)
    """
)


# --------------------------------------------------------------------- a run


@dataclass
class _Run:
    engine: AsyncEngine
    org_id: str
    connection_id: str
    tracker: Tracker
    clock: Callable[[], datetime]
    pause: Callable[[float], Awaitable[None]]
    called: bool = False
    last_write: datetime | None = None
    paused_until: datetime | None = None
    failure: TrackerFailure | None = None
    synced: list[str] = field(default_factory=list)

    @property
    def ids(self) -> dict[str, str]:
        return {"org_id": self.org_id, "connection_id": self.connection_id}

    async def before_call(self) -> None:
        """Step 5: a call goes out only while the connection is still usable (AC37)."""
        async with self.engine.connect() as connection:
            state = await connection.scalar(_STATE, self.ids)
        if state not in _USABLE:
            raise _RunEnds(f"the connection is {state}")
        self.called = True

    async def before_write(self) -> None:
        """Step 6: writes at least `WRITE_SPACING_SECONDS` apart (NFR4); the state is
        read after the wait, right before the call."""
        if self.last_write is not None:
            waited = (self.clock() - self.last_write).total_seconds()
            if waited < WRITE_SPACING_SECONDS:
                await self.pause(WRITE_SPACING_SECONDS - waited)
        await self.before_call()
        self.last_write = self.clock()


def _next_poll(
    case_state: str | None, resolved_at: datetime | None, now: datetime
) -> datetime | None:
    """D49: active every 5 minutes, resolved hourly for 30 days, then not at all."""
    if case_state == INVESTIGATING:
        return now + ACTIVE_POLL
    if (
        case_state == RESOLVED
        and resolved_at is not None
        and resolved_at <= now - FOLLOW_AFTER_RESOLVE
    ):
        return None
    return now + RESOLVED_POLL


def _followed(row: Any, now: datetime) -> bool:
    return not (
        row.case_state == RESOLVED
        and row.case_resolved_at is not None
        and row.case_resolved_at <= now - FOLLOW_AFTER_RESOLVE
    )


def _backoff(failures: int) -> timedelta:
    base: timedelta = min(BACKOFF_START * (2 ** max(failures - 1, 0)), BACKOFF_CEILING)
    # Spread, not secrecy: connections that failed together should not retry together.
    spread = 1 + random.uniform(-BACKOFF_JITTER, BACKOFF_JITTER)
    return base * spread


def _place(row: Any) -> PlaceKey:
    return PlaceKey(external_id=row.place_external_id, key=row.place_key)


def _issue(row: Any) -> IssueRef:
    return IssueRef(external_id=row.external_id, external_key=row.external_key)


def _views(row: Any, now: datetime) -> tuple[LinkView, CaseView]:
    return (
        LinkView(
            role=row.role,
            seen=row.seen_state,
            wanted=row.wanted_state,
            held=row.tracker_closed_at is not None,
        ),
        CaseView(
            state=row.case_state, signal_open=bool(row.signal_open), followed=_followed(row, now)
        ),
    )


async def _lock(connection: AsyncConnection, run: _Run, row: Any) -> Any:
    """The case's finding, then the link, locked and read again (design, *The job*)."""
    await connection.execute(_LOCK_ROOT, {"org_id": run.org_id, "finding_id": row.finding_id})
    return (await connection.execute(_LOCK_LINK, {**run.ids, "link_id": row.id})).one_or_none()


async def _event(
    connection: AsyncConnection, run: _Run, row: Any, kind: str, *, actor: str, **fields: Any
) -> None:
    await record_event(
        connection,
        kind,
        org_id=run.org_id,
        finding_id=row.finding_id,
        at=run.clock(),
        actor_kind=actor,
        link_id=row.id,
        **fields,
    )


async def _owe_email(connection: AsyncConnection, run: _Run, row: Any, kind: str) -> None:
    await connection.execute(
        text(RECORD_EMAIL_OWED_SQL),
        email_owed_parameters(
            org_id=run.org_id, finding_id=row.finding_id, kind=kind, at=run.clock()
        ),
    )


async def _seen(
    connection: AsyncConnection,
    run: _Run,
    row: Any,
    *,
    seen: str | None,
    wanted: str | None,
    held_at: datetime | None,
    status: str = "in_sync",
    failure: str | None = None,
    case_state: str | None = None,
) -> None:
    """The link after a read it acted on: what was seen, and when to look next. A case
    this act moved (`case_state`) is polled on its new schedule."""
    now = run.clock()
    if case_state is None:
        next_sync_at = _next_poll(row.case_state, row.case_resolved_at, now)
    else:
        next_sync_at = _next_poll(case_state, now if case_state == RESOLVED else None, now)
    if next_sync_at is None and status == "in_sync":
        status = "not_followed"
    if wanted is None and row.comment_wanted is not None:
        # A comment is owed and nothing stands before it: due now, so a run that died
        # after the close leaves its comment for the very next run (AC87).
        next_sync_at = now
    await connection.execute(
        _SEEN,
        {
            "org_id": run.org_id,
            "link_id": row.id,
            "seen": seen,
            "wanted": wanted,
            "tracker_closed_at": held_at,
            "status": status,
            "failure": failure,
            "at": now,
            "next_sync_at": next_sync_at,
        },
    )
    run.synced.append(str(row.id))


# --------------------------------------------------------------- creating


async def _create(run: _Run, row: Any) -> None:
    """Design, *Creating without duplicates*: `first_attempt_at` is committed before the
    first create; a retry looks for the half-made issue first; the public check runs
    before every create; the issue is written from the facts frozen at the click."""
    try:
        facts = IssueFacts.from_json(row.frozen_facts)
    except ValueError:
        # A stored copy that no longer reads as facts must never reach a tracker.
        await _refuse(run, row, "write_refused")
        return
    now = run.clock()
    first_try = row.first_attempt_at is None
    if first_try:
        fault("before_first_attempt_commit")
        async with run.engine.begin() as connection:
            await connection.execute(
                _FIRST_ATTEMPT, {"org_id": run.org_id, "link_id": row.id, "at": now}
            )
        since = now
    else:
        since = row.first_attempt_at
        settled = since + timedelta(seconds=run.tracker.search_settles_seconds)
        if now < settled:
            # The tracker's search may not see an issue that early (design): look later.
            async with run.engine.begin() as connection:
                await connection.execute(
                    _SETTLING, {"org_id": run.org_id, "link_id": row.id, "next_sync_at": settled}
                )
            return
        await run.before_call()
        found = run.tracker.find_existing(_place(row), facts.link_back, since)
        if found is not None:
            await _created(run, row, found)
            return
    if not await _public_check_passes(run, row):
        return
    fault("before_create")
    await run.before_write()
    created = run.tracker.create(_place(row), issue_text(facts))
    fault("after_reply_before_commit")
    await _created(run, row, created)


async def _public_check_passes(run: _Run, row: Any) -> bool:
    """BR12, AC51: the allowance and GitHub's own answer, read immediately before
    creating, every try. A failed answer creates nothing (it raises, and the link waits)."""
    async with run.engine.connect() as connection:
        fresh = (await connection.execute(_READ_LINK, {**run.ids, "link_id": row.id})).one_or_none()
    if fresh is None or fresh.sync_status != "creating" or fresh.place_external_id is None:
        return False
    await run.before_call()
    if run.tracker.is_public(_place(fresh)) and fresh.public_allowed_at is None:
        await _refuse(run, row, "public_repository")
        return False
    return True


async def _refuse(run: _Run, row: Any, failure: str) -> None:
    async with run.engine.begin() as connection:
        locked = await _lock(connection, run, row)
        if locked is None or locked.sync_status != "creating":
            return
        await connection.execute(
            _REFUSED, {"org_id": run.org_id, "link_id": row.id, "failure": failure}
        )
        await _event(connection, run, row, "tracker_write_failed", actor="system", failure=failure)


async def _created(run: _Run, row: Any, issue: IssueRef) -> None:
    now = run.clock()
    async with run.engine.begin() as connection:
        locked = await _lock(connection, run, row)
        if locked is None or locked.sync_status != "creating":
            # Removed or stopped while the issue was written: the issue stays, with its
            # link back, and nothing here points to it (stated in `status.md`).
            logger.warning(
                "an issue was created for a link no longer creating", link_id=str(row.id)
            )
            return
        pending = locked.wanted_state is not None and locked.wanted_state != OPEN
        await connection.execute(
            _CREATED,
            {
                "org_id": run.org_id,
                "link_id": row.id,
                "external_id": issue.external_id,
                "external_key": issue.external_key,
                "at": now,
                # A resolve while it was being created owes a close: read again at once.
                "next_sync_at": now
                if pending
                else _next_poll(locked.case_state, locked.case_resolved_at, now),
            },
        )
        await _event(connection, run, row, "issue_created", actor="system")
    run.synced.append(str(row.id))


# -------------------------------------------------------------- reconciling


async def _reconcile(run: _Run, row: Any) -> None:
    now = run.clock()
    if not _followed(row, now) and row.wanted_state is None:
        # FR39, AC85: past 30 days after the case resolved, the link is no longer read.
        await _set_status(run, row, "not_followed", next_sync_at=None)
        return
    await run.before_call()
    read = run.tracker.read(_place(row), _issue(row))
    fixed = compare_read(read)
    if fixed is not None:
        await _apply_read(run, row, fixed, read)
        return
    assert isinstance(read, IssueSeen)
    link, case = _views(row, now)
    action = compare(link, read.state, case)
    if action is Action.WRITE_WANTED:
        await _write_wanted(run, row)
        return
    await _apply(run, row, read)
    await _comment_if_owed(run, row)


async def _apply(run: _Run, row: Any, read: IssueSeen) -> None:
    """Rows 1, 3 to 6 and 7, under the locks, compared again with what is now true."""
    now = run.clock()
    async with run.engine.begin() as connection:
        locked = await _lock(connection, run, row)
        if locked is None or locked.sync_status in ("stopped", "creating"):
            return
        link, case = _views(locked, now)
        action = compare(link, read.state, case)
        if action is Action.WRITE_WANTED:
            # A person acted since the read: the next run writes what they want.
            await connection.execute(
                _SETTLING, {"org_id": run.org_id, "link_id": row.id, "next_sync_at": now}
            )
            return
        await _act(connection, run, locked, action, read)


async def _act(
    connection: AsyncConnection, run: _Run, row: Any, action: Action, read: IssueSeen
) -> None:
    tracker, now = read.state, run.clock()
    held = row.tracker_closed_at
    if action is Action.NOTHING:
        blocked = row.last_failure == "reopen_blocked"
        await _seen(
            connection,
            run,
            row,
            seen=row.seen_state,
            wanted=row.wanted_state,
            held_at=held,
            status="needs_attention" if blocked else "in_sync",
            failure="reopen_blocked" if blocked else None,
        )
    elif action in (Action.ACCEPT_LANDED, Action.RECORD_ONLY):
        await _seen(connection, run, row, seen=tracker, wanted=None, held_at=held)
    elif action is Action.REOPEN_CASE:
        await _reopen(connection, run, row, read)
    elif action is Action.END_HOLD:
        await connection.execute(
            text(CLEAR_HOLD_SQL),
            clear_hold_parameters(org_id=run.org_id, finding_id=row.finding_id),
        )
        await _event(connection, run, row, "issue_reopened", actor="tracker")
        await _seen(connection, run, row, seen=tracker, wanted=None, held_at=None)
    elif action is Action.HOLD:
        await _event(
            connection, run, row, "issue_closed", actor="tracker", close_reason=read.close_reason
        )
        await _owe_email(connection, run, row, "tracker_closed_signal_open")
        await _seen(connection, run, row, seen=tracker, wanted=None, held_at=now)
    elif action in (Action.RESOLVE_AFTER_HOLD, Action.RESOLVE_CLOSED):
        if action is Action.RESOLVE_CLOSED:
            await _event(
                connection,
                run,
                row,
                "issue_closed",
                actor="tracker",
                close_reason=read.close_reason,
            )
        await end_case(
            connection,
            org_id=run.org_id,
            root_id=row.finding_id,
            resolution="tracker_closed",
            at=now,
            actor_kind="tracker",
        )
        await _owe_email(connection, run, row, "tracker_closed")
        await _seen(
            connection, run, row, seen=tracker, wanted=None, held_at=None, case_state=RESOLVED
        )


async def _reopen(connection: AsyncConnection, run: _Run, row: Any, read: IssueSeen) -> None:
    """Row 4, or row 7 when another case holds the service (BR7): the index decides."""
    now = run.clock()
    await _event(connection, run, row, "issue_reopened", actor="tracker")
    savepoint = await connection.begin_nested()
    try:
        reopened = await connection.execute(
            text(REOPEN_CASE_SQL),
            reopen_case_parameters(org_id=run.org_id, finding_id=row.finding_id, at=now),
        )
        await savepoint.commit()
    except IntegrityError:
        await savepoint.rollback()
        await _seen(
            connection,
            run,
            row,
            seen=read.state,
            wanted=None,
            held_at=None,
            status="needs_attention",
            failure="reopen_blocked",
        )
        return
    if reopened.first() is None:
        await _seen(connection, run, row, seen=read.state, wanted=None, held_at=None)
        return
    joined = await connection.execute(
        text(JOIN_OPEN_SQL),
        join_open_parameters(
            org_id=run.org_id,
            project_id=row.project_id,
            scope=row.scope,
            service_name=row.service_name,
            root_finding_id=row.finding_id,
        ),
    )
    for joined_id in joined.scalars():
        await record_event(
            connection,
            "finding_joined_case",
            org_id=run.org_id,
            finding_id=row.finding_id,
            at=now,
            actor_kind="tracker",
            joined_finding_id=joined_id,
        )
    await record_event(
        connection,
        "case_reopened",
        org_id=run.org_id,
        finding_id=row.finding_id,
        at=now,
        actor_kind="tracker",
    )
    await record_audit(
        connection,
        org_id=run.org_id,
        root_id=row.finding_id,
        action=CASE_REOPENED_ACTION,
        detail={},
    )
    await _seen(
        connection, run, row, seen=read.state, wanted=None, held_at=None, case_state=INVESTIGATING
    )


async def _write_wanted(run: _Run, row: Any) -> None:
    """Row 2: write W, then commit S := W; the comment after, at most once (AC87)."""
    wanted = row.wanted_state
    await run.before_write()
    run.tracker.set_status(_place(row), _issue(row), wanted, close_reason_for(row.comment_wanted))
    async with run.engine.begin() as connection:
        locked = await _lock(connection, run, row)
        if locked is None or locked.sync_status in ("stopped", "creating"):
            return
        # A person changed what they want since: S records what was written, W stays.
        still = locked.wanted_state if locked.wanted_state != wanted else None
        kind = "issue_closed" if wanted == CLOSED else "issue_reopened"
        fields = {"close_reason": close_reason_for(row.comment_wanted)} if wanted == CLOSED else {}
        await _event(connection, run, locked, kind, actor="system", **fields)
        await _seen(connection, run, locked, seen=wanted, wanted=still, held_at=None)
    fault("after_close_before_comment_commit")
    await _comment_if_owed(run, row)


async def _comment_if_owed(run: _Run, row: Any) -> None:
    """The one comment owed, once nothing is pending on the status (design: close,
    commit S and W, commit the comment as written, then post)."""
    async with run.engine.connect() as connection:
        fresh = (await connection.execute(_READ_LINK, {**run.ids, "link_id": row.id})).one_or_none()
    if fresh is None or fresh.comment_wanted is None or fresh.wanted_state is not None:
        return
    kind = fresh.comment_wanted
    if kind == IDLE_WARNING and fresh.case_state != INVESTIGATING:
        # The case ended before the warning went: it is owed no more (AC97).
        text_ = None
    else:
        ends_on = (
            (fresh.case_activity_at + CASE_IDLE_LIMIT).date()
            if kind == IDLE_WARNING and fresh.case_activity_at is not None
            else None
        )
        text_ = comment_text(kind, ends_on=ends_on)
    async with run.engine.begin() as connection:
        now = run.clock()
        taken = await connection.execute(
            _TAKE_COMMENT,
            {
                "org_id": run.org_id,
                "link_id": row.id,
                "kind": kind,
                "at": now,
                "next_sync_at": _next_poll(fresh.case_state, fresh.case_resolved_at, now),
            },
        )
        if taken.first() is None or text_ is None:
            return
    fault("after_comment_commit_before_post")
    await _post(run, fresh, text_)


async def _post(run: _Run, row: Any, words: CommentText) -> None:
    await run.before_write()
    try:
        run.tracker.comment(_place(row), _issue(row), words)
    except TrackerFailure as failure:
        # Taken and not posted: "at most one" is the promise, so it is not retried.
        logger.warning("a comment was lost", link_id=str(row.id), failure=failure.failure)
        if failure.failure in _ENDS_THE_RUN:
            raise
        async with run.engine.begin() as connection:
            await _event(
                connection,
                run,
                row,
                "tracker_write_failed",
                actor="system",
                failure="write_refused",
            )


async def _apply_read(run: _Run, row: Any, action: Action, read: Any) -> None:
    """Rows 8 to 11: the case is never changed by any of them."""
    now = run.clock()
    async with run.engine.begin() as connection:
        locked = await _lock(connection, run, row)
        if locked is None or locked.sync_status in ("stopped", "creating"):
            return
        if action is Action.GONE:
            if locked.sync_status != "gone":
                await _event(connection, run, locked, "issue_gone", actor="tracker")
            await connection.execute(
                _SET_STATUS,
                _status(
                    run,
                    locked,
                    "gone",
                    next_sync_at=None,
                    gone_at=locked.gone_at or now,
                    synced=now,
                ),
            )
        elif action is Action.GONE_RECHECK:
            gone_at = locked.gone_at or now
            if locked.sync_status != "gone":
                await _event(connection, run, locked, "issue_gone", actor="tracker")
            again = now + GONE_RECHECK if now - gone_at < GONE_RECHECK_FOR else None
            await connection.execute(
                _SET_STATUS,
                _status(run, locked, "gone", next_sync_at=again, gone_at=gone_at, synced=now),
            )
        elif action is Action.PLACE_UNREACHABLE:
            await connection.execute(
                _UNREACHABLE_PLACE, {"org_id": run.org_id, "place_id": locked.place_id}
            )
            await connection.execute(
                _SET_STATUS,
                _status(
                    run,
                    locked,
                    "needs_attention",
                    next_sync_at=None,
                    failure="repository_unreachable",
                ),
            )
        elif action is Action.MOVED:
            await _move(connection, run, locked, read)


async def _move(connection: AsyncConnection, run: _Run, row: Any, moved: IssueMoved) -> None:
    """Row 11: inside a connected repository, follow it; outside, say so."""
    await _event(connection, run, row, "issue_moved", actor="tracker")
    place = None
    key = None
    if moved.repository is not None and moved.number is not None and moved.issue_id is not None:
        key = github_issue_key(moved.repository, moved.number)
        place = await connection.scalar(_PLACE_BY_KEY, {**run.ids, "key": moved.repository})
    if place is None or key is None:
        await connection.execute(
            _SET_STATUS,
            _status(run, row, "needs_attention", next_sync_at=None, failure="moved_out_of_reach"),
        )
        return
    await connection.execute(
        _MOVE,
        {
            "org_id": run.org_id,
            "link_id": row.id,
            "external_id": moved.issue_id,
            "external_key": key,
            "place_id": place,
            "at": run.clock(),
        },
    )


def _status(
    run: _Run,
    row: Any,
    status: str,
    *,
    next_sync_at: datetime | None,
    failure: str | None = None,
    gone_at: datetime | None = None,
    synced: datetime | None = None,
) -> dict[str, Any]:
    return {
        "org_id": run.org_id,
        "link_id": row.id,
        "status": status,
        "failure": failure,
        "gone_at": gone_at,
        "next_sync_at": next_sync_at,
        "synced_at": synced,
    }


async def _set_status(run: _Run, row: Any, status: str, *, next_sync_at: datetime | None) -> None:
    async with run.engine.begin() as connection:
        await connection.execute(_SET_STATUS, _status(run, row, status, next_sync_at=next_sync_at))


# -------------------------------------------------------------- failures


async def _link_failed(run: _Run, row: Any, failure: TrackerFailure) -> None:
    """A call about one link failed. What may pass leaves it `waiting` with backoff;
    a repository gone, or a write refused for good, needs a person."""
    now = run.clock()
    async with run.engine.begin() as connection:
        if failure.failure == "repository_unreachable" and row.place_id is not None:
            await connection.execute(
                _UNREACHABLE_PLACE, {"org_id": run.org_id, "place_id": row.place_id}
            )
            if row.sync_status != "creating":
                await connection.execute(
                    _SET_STATUS,
                    _status(
                        run,
                        row,
                        "needs_attention",
                        next_sync_at=None,
                        failure="repository_unreachable",
                    ),
                )
                return
        if failure.failure == "write_refused" and row.sync_status != "creating":
            await connection.execute(
                _SET_STATUS,
                _status(run, row, "needs_attention", next_sync_at=None, failure="write_refused"),
            )
            await _event(
                connection,
                run,
                row,
                "tracker_write_failed",
                actor="system",
                failure="write_refused",
            )
            return
        retry = now + (
            timedelta(seconds=failure.retry_after)
            if failure.retry_after is not None
            else _backoff(row.failures + 1)
        )
        await connection.execute(
            _WAITING,
            {
                "org_id": run.org_id,
                "link_id": row.id,
                "failure": _link_failure(failure),
                "next_sync_at": retry,
            },
        )


def _link_failure(failure: TrackerFailure) -> str:
    known = (
        "unreachable",
        "timed_out",
        "server_error",
        "rate_limited",
        "unauthorized",
        "repository_unreachable",
        "write_refused",
    )
    return failure.failure if failure.failure in known else "unexpected_answer"


# -------------------------------------------------------------- the job


async def sync_connection(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection_id: str,
    keys: ConfiguredKeys,
    tracker_of: Callable[[StoredConnection], Tracker],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[str]:
    """One run for one connection. Returns the ids of the links it synced."""
    ids = {"org_id": org_id, "connection_id": connection_id}
    now = clock()
    loaded = await load_connection(engine, org_id, connection_id)
    if loaded is None:
        return []
    until = now + timedelta(seconds=LEASE_SECONDS)
    async with engine.begin() as connection:
        leased = await connection.scalar(_TAKE_LEASE, {**ids, "until": until, "now": now})
    if leased is None:
        logger.info("a sync found its connection's lease held", connection_id=connection_id)
        return []
    try:
        row = loaded.row
        if row.state not in _USABLE or (row.paused_until is not None and row.paused_until > now):
            return []
        try:
            tracker = tracker_of(stored_connection(loaded, keys))
        except TrackerFailure as failure:
            await _connection_failed(engine, loaded, failure, now=now)
            return []
        run = _Run(engine, org_id, connection_id, tracker, clock, pause)
        try:
            await _links(run)
        finally:
            tracker.finish()
        await _stand(engine, loaded, run, now=clock())
        return run.synced
    finally:
        async with engine.begin() as connection:
            await connection.execute(_RELEASE_LEASE, {**ids, "until": until})


async def _links(run: _Run) -> None:
    async with run.engine.connect() as connection:
        due = list(
            await connection.execute(
                _DUE_LINKS, {**run.ids, "now": run.clock(), "limit": LINKS_PER_RUN}
            )
        )
    for each in due:
        async with run.engine.connect() as connection:
            row = (
                await connection.execute(_READ_LINK, {**run.ids, "link_id": each.id})
            ).one_or_none()
        if row is None or row.sync_status in ("stopped", "refused"):
            continue
        if row.place_external_id is None:
            # Its repository left the connection (`SET NULL`): nothing to read or write.
            await _placeless(run, row)
            continue
        try:
            if row.sync_status == "creating":
                await _create(run, row)
            elif row.sync_status != "promoting":
                await _reconcile(run, row)
        except _RunEnds as ended:
            logger.info("a sync run stopped", connection_id=run.connection_id, why=str(ended))
            return
        except TrackerFailure as failure:
            await _link_failed(run, row, failure)
            if failure.failure in _ENDS_THE_RUN:
                run.failure = failure
                await _pause(run, failure, row.failures + 1)
                return


async def _placeless(run: _Run, row: Any) -> None:
    """A link whose repository row is gone: a creation is refused, any other link
    needs a person and is no longer read. Neither calls the tracker."""
    if row.sync_status == "creating":
        await _refuse(run, row, "repository_unreachable")
        return
    async with run.engine.begin() as connection:
        await connection.execute(
            _SET_STATUS,
            _status(
                run, row, "needs_attention", next_sync_at=None, failure="repository_unreachable"
            ),
        )


async def _pause(run: _Run, failure: TrackerFailure, failures: int) -> None:
    """Step 7: the run ends and the connection waits, as long as a limit asked."""
    wait = (
        timedelta(seconds=failure.retry_after)
        if failure.retry_after is not None
        else _backoff(failures)
    )
    run.paused_until = run.clock() + wait
    async with run.engine.begin() as connection:
        await connection.execute(_PAUSE, {**run.ids, "until": run.paused_until})
    logger.warning(
        "a sync run ended at a tracker failure",
        connection_id=run.connection_id,
        failure=failure.failure,
        status=failure.status,
    )


async def _stand(engine: AsyncEngine, loaded: Loaded, run: _Run, *, now: datetime) -> None:
    """The connection's state after the run (AC79's moves), through the one machine.
    A run's ending failure is always one a connection can hold (`_ENDS_THE_RUN`)."""
    if run.failure is not None:
        standing = states.after_failure(
            loaded.standing, run.failure.failure, run.failure.status, now=now
        )
    elif run.called:
        standing = states.after_success(loaded.standing, now=now)
    else:
        return
    await write_standing(engine, run.org_id, run.connection_id, standing)


async def _connection_failed(
    engine: AsyncEngine, loaded: Loaded, failure: TrackerFailure, *, now: datetime
) -> None:
    standing = states.after_failure(loaded.standing, failure.failure, failure.status, now=now)
    await write_standing(engine, str(loaded.row.org_id), str(loaded.row.id), standing)


# ------------------------------------------------------------- the sweep


async def sweep_connections(engine: AsyncEngine, *, now: datetime) -> tuple[int, int]:
    """Add one sync job per connection with a due link, and each connection's hourly
    refresh, by a conditional insert (AC48). Returns how many of each it considered."""
    async with engine.connect() as connection:
        due = list(
            await connection.execute(
                _CONNECTIONS_DUE,
                {
                    "now": now,
                    "refresh_before": now - REPOSITORY_REFRESH,
                    "kinds": list(TRACKER_KINDS),
                },
            )
        )
    syncs = refreshes = 0
    async with engine.begin() as connection:
        for row in due:
            org_id, connection_id = str(row.org_id), str(row.id)
            if row.sync_due:
                await connection.execute(
                    text(ENQUEUE_UNLESS_WAITING),
                    sync_job(org_id=org_id, connection_id=connection_id).parameters(),
                )
                syncs += 1
            if row.refresh_due:
                await connection.execute(
                    text(ENQUEUE_UNLESS_WAITING),
                    refresh_job(org_id=org_id, connection_id=connection_id).parameters(),
                )
                refreshes += 1
    return syncs, refreshes
