"""`cases_sweep`, against real Postgres (F22 plan T2.6; AC10's sweep half, AC17).

The sweep ends what a person's silence ends — an idle case at 90 days, an
untouched one 72 hours after recovery — warns before the idle end, and heals what
a race left behind: a finding that missed its join, one left on a resolved root,
and an assignee who left or became a viewer while their case was locked.

Each case is its own transaction, locked `FOR NO KEY UPDATE SKIP LOCKED`, so the
sweep never waits on a person or the detection job and so cannot deadlock against
either. This suite fails rather than skips when the stack is down; see
tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from alert_fixtures import AlertTenant, add_finding, postgres_reachable, rows
from live import require_stack
from nexovue_platform.cases import (
    CASE_IDLE_LIMIT,
    CASE_IDLE_WARNING_AHEAD,
    CASE_JOIN_WINDOW,
    CASE_UNTOUCHED_AFTER,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler import cases

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_cases_sweep_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

NOW: Final = datetime(2026, 11, 2, 14, 2, tzinfo=UTC)
HOUR: Final = timedelta(hours=1)
DAY: Final = timedelta(days=1)
SERVICE: Final = "checkout"
# The sweep that skips a held row must not wait for it; this is far longer than a skip.
_NEVER_WAITS_SECONDS: Final = 5.0


async def _finding(
    engine: AsyncEngine,
    tenant: AlertTenant,
    *,
    service: str | None = SERVICE,
    started_at: datetime,
    closed_at: datetime | None = None,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return await add_finding(
            connection, tenant, service=service, started_at=started_at, closed_at=closed_at
        )


async def _set(
    engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID, **columns: Any
) -> None:
    """Case columns as the API's operations leave them; names are this file's own."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with engine.begin() as connection:
        await connection.execute(
            text(f"UPDATE findings SET {assignments} WHERE org_id = :org_id AND id = :id"),
            {"org_id": tenant.org_id, "id": finding, **columns},
        )


async def _active_case(
    engine: AsyncEngine,
    tenant: AlertTenant,
    finding: uuid.UUID,
    *,
    acted_at: datetime,
    worked: bool = True,
    assignee: uuid.UUID | None = None,
) -> None:
    await _set(
        engine,
        tenant,
        finding,
        case_state="investigating",
        case_opened_at=acted_at,
        case_activity_at=acted_at,
        case_worked_at=acted_at if worked else None,
        assignee_id=assignee,
    )


async def _row(engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID) -> Any:
    (found,) = await rows(
        engine,
        "SELECT * FROM findings WHERE org_id = :org_id AND id = :id",
        org_id=tenant.org_id,
        id=finding,
    )
    return found


async def _events(engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID) -> list[Any]:
    return await rows(
        engine,
        "SELECT kind, actor_kind, actor_id, detail FROM finding_events "
        "WHERE org_id = :org_id AND finding_id = :id ORDER BY at, id",
        org_id=tenant.org_id,
        id=finding,
    )


async def _owner(engine: AsyncEngine, tenant: AlertTenant) -> uuid.UUID:
    (owner,) = await rows(
        engine, "SELECT id FROM users WHERE org_id = :org_id", org_id=tenant.org_id
    )
    found: uuid.UUID = owner.id
    return found


async def _member(engine: AsyncEngine, tenant: AlertTenant, *, role: str = "member") -> uuid.UUID:
    async with engine.begin() as connection:
        made: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO users (org_id, email, role, password_hash) "
                    "VALUES (:org_id, :email, :role, 'not-a-hash') RETURNING id"
                ),
                {
                    "org_id": tenant.org_id,
                    "email": f"member-{uuid.uuid4().hex[:12]}@example.test",
                    "role": role,
                },
            )
        ).scalar_one()
    return made


# ------------------------------------------------------------ ending by silence


async def test_an_idle_case_ends_at_90_days_and_a_stream_of_joins_does_not_extend_it(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """BR13, FR26, D42: no person's act for 90 days resolves the case as
    `no_activity`, with its event and an audit row naming nobody. Findings joining
    are not activity, however many and however recent."""
    root = await _finding(engine, tenant, started_at=NOW - 100 * DAY, closed_at=NOW - 99 * DAY)
    await _active_case(engine, tenant, root, acted_at=NOW - CASE_IDLE_LIMIT - HOUR)
    for days_ago in (30, 10, 2):
        joined = await _finding(
            engine,
            tenant,
            started_at=NOW - days_ago * DAY,
            closed_at=NOW - days_ago * DAY + HOUR,
        )
        await _set(engine, tenant, joined, root_finding_id=root)
    fresh = await _finding(engine, tenant, service="cart", started_at=NOW - 2 * DAY)
    await _active_case(engine, tenant, fresh, acted_at=NOW - CASE_IDLE_LIMIT + HOUR)

    outcome = await cases.sweep(engine, now=NOW)

    ended = await _row(engine, tenant, root)
    assert (ended.case_state, ended.case_resolution) == ("resolved", "no_activity")
    assert ended.case_resolved_at == NOW and ended.case_resolved_by is None
    assert (await _row(engine, tenant, fresh)).case_state == "investigating"
    assert outcome.ended_idle >= 1
    assert [
        (event.kind, event.actor_kind, event.detail)
        for event in await _events(engine, tenant, root)
    ] == [("case_resolved", "system", {"resolution": "no_activity"})]
    audited = await rows(
        engine,
        "SELECT actor_user_id, action, detail FROM audit_log WHERE org_id = :org_id "
        "AND target = :target",
        org_id=tenant.org_id,
        target=f"finding:{root}",
    )
    assert [(row.actor_user_id, row.action, row.detail) for row in audited] == [
        (None, "case.resolved", {"resolution": "no_activity"})
    ]


async def test_ending_a_case_detaches_its_open_joined_finding(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR6, D44: however a case resolves, an open joined finding leaves it in that
    transaction; a closed one stays attached, for the timeline."""
    root = await _finding(engine, tenant, started_at=NOW - 100 * DAY, closed_at=NOW - 99 * DAY)
    await _active_case(engine, tenant, root, acted_at=NOW - CASE_IDLE_LIMIT - HOUR)
    closed = await _finding(engine, tenant, started_at=NOW - 3 * DAY, closed_at=NOW - 2 * DAY)
    still_open = await _finding(engine, tenant, started_at=NOW - HOUR)
    await _set(engine, tenant, closed, root_finding_id=root)
    await _set(engine, tenant, still_open, root_finding_id=root)

    await cases.sweep(engine, now=NOW)

    assert (await _row(engine, tenant, still_open)).root_finding_id is None
    assert (await _row(engine, tenant, closed)).root_finding_id == root
    kinds = [(event.kind, event.detail) for event in await _events(engine, tenant, root)]
    assert ("finding_left_case", {"left_finding_id": str(still_open)}) in kinds


async def test_an_untouched_case_ends_72_hours_after_its_last_finding_closed(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR49, D71: taken and nothing more, with the root and every joined finding
    closed for 72 hours, the case ends as `recovered_untouched`. A note or any act
    beyond taking (`case_worked_at`) keeps it; so does a joined finding that closed
    more lately, or one still open."""
    long_ago = NOW - CASE_UNTOUCHED_AFTER - HOUR
    untouched = await _finding(engine, tenant, started_at=long_ago - DAY, closed_at=long_ago)
    await _active_case(engine, tenant, untouched, acted_at=long_ago - DAY, worked=False)
    worked = await _finding(
        engine, tenant, service="cart", started_at=long_ago - DAY, closed_at=long_ago
    )
    await _active_case(engine, tenant, worked, acted_at=long_ago - DAY, worked=True)
    recent_join = await _finding(
        engine, tenant, service="search", started_at=long_ago - DAY, closed_at=long_ago
    )
    await _active_case(engine, tenant, recent_join, acted_at=long_ago - DAY, worked=False)
    joined = await _finding(
        engine, tenant, service="search", started_at=NOW - 2 * DAY, closed_at=NOW - DAY
    )
    await _set(engine, tenant, joined, root_finding_id=recent_join)
    still_open = await _finding(engine, tenant, service="pay", started_at=long_ago - DAY)
    await _active_case(engine, tenant, still_open, acted_at=long_ago - DAY, worked=False)

    outcome = await cases.sweep(engine, now=NOW)

    ended = await _row(engine, tenant, untouched)
    assert (ended.case_state, ended.case_resolution) == ("resolved", "recovered_untouched")
    for kept in (worked, recent_join, still_open):
        assert (await _row(engine, tenant, kept)).case_state == "investigating"
    assert outcome.ended_untouched == 1


async def test_the_idle_warning_starts_once_and_owes_one_email(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR26, D90: at 76 days the warning starts — its marker, and one
    `idle_warning` row in `finding_emails_owed` — and a second sweep owes nothing
    more. An act after it starts a new spell, which warns afresh."""
    root = await _finding(engine, tenant, started_at=NOW - 80 * DAY, closed_at=NOW - 79 * DAY)
    warn_from = CASE_IDLE_LIMIT - CASE_IDLE_WARNING_AHEAD
    await _active_case(engine, tenant, root, acted_at=NOW - warn_from - HOUR)

    await cases.sweep(engine, now=NOW)
    await cases.sweep(engine, now=NOW + HOUR)

    assert (await _row(engine, tenant, root)).case_warned_at == NOW
    owed = await rows(
        engine,
        "SELECT kind FROM finding_emails_owed WHERE org_id = :org_id AND finding_id = :id",
        org_id=tenant.org_id,
        id=root,
    )
    assert [row.kind for row in owed] == ["idle_warning"]

    acted = NOW + 2 * HOUR
    await _set(engine, tenant, root, case_activity_at=acted)
    later = acted + warn_from + HOUR
    await cases.sweep(engine, now=later)
    assert (await _row(engine, tenant, root)).case_warned_at == later
    owed = await rows(
        engine,
        "SELECT count(*) AS owed FROM finding_emails_owed WHERE org_id = :org_id "
        "AND finding_id = :id",
        org_id=tenant.org_id,
        id=root,
    )
    assert owed[0].owed == 2


# --------------------------------------------------------- healing a race


async def test_a_finding_that_missed_its_join_joins_within_the_next_sweep(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC17's first half: a person started a case on an earlier finding as the job
    inserted the open one, so it missed joining. The next sweep joins it, with the
    same 7-day condition the job's insert uses; a quiet case takes nothing."""
    root = await _finding(engine, tenant, started_at=NOW - 2 * DAY, closed_at=NOW - DAY)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR)
    missed = await _finding(engine, tenant, started_at=NOW - HOUR)
    quiet_root = await _finding(
        engine, tenant, service="cart", started_at=NOW - 20 * DAY, closed_at=NOW - 19 * DAY
    )
    await _active_case(engine, tenant, quiet_root, acted_at=NOW - CASE_JOIN_WINDOW - HOUR)
    standalone = await _finding(engine, tenant, service="cart", started_at=NOW - HOUR)

    outcome = await cases.sweep(engine, now=NOW)

    assert (await _row(engine, tenant, missed)).root_finding_id == root
    assert (await _row(engine, tenant, standalone)).root_finding_id is None
    assert outcome.joined == 1
    assert [(event.kind, event.detail) for event in await _events(engine, tenant, root)] == [
        ("finding_joined_case", {"joined_finding_id": str(missed)})
    ]


async def test_an_open_finding_left_on_a_resolved_root_leaves_within_the_next_sweep(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC17's second half: a person resolved as the job inserted a joined finding,
    whose insert read the case as still active. The next sweep detaches it."""
    root = await _finding(engine, tenant, started_at=NOW - 2 * DAY, closed_at=NOW - DAY)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR)
    stranded = await _finding(engine, tenant, started_at=NOW - HOUR)
    await _set(engine, tenant, stranded, root_finding_id=root)
    await _set(
        engine,
        tenant,
        root,
        case_state="resolved",
        case_resolution="resolved",
        case_resolved_at=NOW - HOUR,
    )

    outcome = await cases.sweep(engine, now=NOW)

    assert (await _row(engine, tenant, stranded)).root_finding_id is None
    assert outcome.left == 1
    assert [(event.kind, event.detail) for event in await _events(engine, tenant, root)] == [
        ("finding_left_case", {"left_finding_id": str(stranded)})
    ]


async def test_an_assignee_who_left_or_became_a_viewer_is_cleared_by_the_next_sweep(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC10's sweep half: removal and demotion clear assignments with `SKIP
    LOCKED`, so a case locked at that moment keeps its assignee; the next sweep
    clears it, with the same system `unassigned` event the routes write."""
    removed = await _member(engine, tenant)
    demoted = await _member(engine, tenant)
    working = await _member(engine, tenant)
    cases_of: dict[uuid.UUID, uuid.UUID] = {}
    for index, person in enumerate((removed, demoted, working)):
        finding = await _finding(engine, tenant, service=f"service-{index}", started_at=NOW - HOUR)
        await _active_case(engine, tenant, finding, acted_at=NOW - HOUR, assignee=person)
        cases_of[person] = finding
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET removed_at = :at WHERE org_id = :org_id AND id = :id"),
            {"org_id": tenant.org_id, "id": removed, "at": NOW - HOUR},
        )
        await connection.execute(
            text("UPDATE users SET role = 'viewer' WHERE org_id = :org_id AND id = :id"),
            {"org_id": tenant.org_id, "id": demoted},
        )

    outcome = await cases.sweep(engine, now=NOW)

    for person in (removed, demoted):
        cleared = await _row(engine, tenant, cases_of[person])
        assert cleared.assignee_id is None and cleared.assignee_acted_at is None
        assert [
            (event.kind, event.actor_kind, event.detail)
            for event in await _events(engine, tenant, cases_of[person])
        ] == [("unassigned", "system", {"assignee_id": str(person)})]
    assert (await _row(engine, tenant, cases_of[working])).assignee_id == working
    assert outcome.unassigned == 2


async def test_a_case_another_transaction_holds_is_skipped_never_waited_on(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC10's "no deadlock against the job": the sweep takes every lock `SKIP
    LOCKED`, so a row the job or a person holds is left for the next sweep, and
    the sweep finishes at once instead of waiting — a sweep that never waits can
    never be half of a deadlock."""
    viewer = await _member(engine, tenant, role="viewer")
    held = await _finding(engine, tenant, started_at=NOW - HOUR)
    await _active_case(engine, tenant, held, acted_at=NOW - HOUR, assignee=viewer)

    async with engine.connect() as holder:
        await holder.begin()
        await holder.execute(
            text("SELECT id FROM findings WHERE org_id = :org_id AND id = :id FOR UPDATE"),
            {"org_id": tenant.org_id, "id": held},
        )
        skipped = await asyncio.wait_for(cases.sweep(engine, now=NOW), _NEVER_WAITS_SECONDS)
        await holder.rollback()

    assert skipped.unassigned == 0
    assert (await _row(engine, tenant, held)).assignee_id == viewer
    cleared = await cases.sweep(engine, now=NOW)
    assert cleared.unassigned == 1
    assert (await _row(engine, tenant, held)).assignee_id is None


async def test_a_sweep_with_nothing_to_do_changes_nothing(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """A case a person acted on lately, with its assignee working, is left alone."""
    owner = await _owner(engine, tenant)
    root = await _finding(engine, tenant, started_at=NOW - HOUR)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR, assignee=owner)

    await cases.sweep(engine, now=NOW)

    kept = await _row(engine, tenant, root)
    assert (kept.case_state, kept.assignee_id, kept.case_warned_at) == (
        "investigating",
        owner,
        None,
    )
    assert await _events(engine, tenant, root) == []


# ------------------------------------------------------------- Step 4 (T4.3, T4.6)


async def _place(engine: AsyncEngine, tenant: AlertTenant) -> tuple[uuid.UUID, uuid.UUID]:
    async with engine.begin() as connection:
        connection_id = (
            await connection.execute(
                text(
                    "INSERT INTO connections (org_id, kind, state, sealed_credential, "
                    "github_app_id, github_installation_id) VALUES (:org, 'github', 'working', "
                    ":key, :app, 1) RETURNING id"
                ),
                {"org": tenant.org_id, "key": b"sealed", "app": uuid.uuid4().int % 10**9},
            )
        ).scalar_one()
        place_id = (
            await connection.execute(
                text(
                    "INSERT INTO connection_repositories (org_id, connection_id, external_id, key, "
                    "is_private) VALUES (:org, :connection, '42', 'acme/api', true) RETURNING id"
                ),
                {"org": tenant.org_id, "connection": connection_id},
            )
        ).scalar_one()
    return connection_id, place_id


async def _synced_link(
    engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID, number: int = 1
) -> uuid.UUID:
    connection_id, place_id = await _place(engine, tenant)
    async with engine.begin() as connection:
        link_id: uuid.UUID = (
            await connection.execute(
                text(
                    "INSERT INTO finding_links (org_id, finding_id, connection_id, place_id, role, "
                    "sync_status, external_id, external_key, seen_state) VALUES (:org, :finding, "
                    ":connection, :place, 'synced', 'in_sync', :id, :key, 'open') RETURNING id"
                ),
                {
                    "org": tenant.org_id,
                    "finding": finding,
                    "connection": connection_id,
                    "place": place_id,
                    "id": str(5000000 + number),
                    "key": f"acme/api#{number}",
                },
            )
        ).scalar_one()
    return link_id


async def test_a_case_with_a_link_is_never_ended_as_untouched(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC90: a link stops the untouched end, as a note does, and neither acknowledges.
    Even a link whose act left `case_worked_at` unset holds it (design step 3)."""
    long_ago = NOW - CASE_UNTOUCHED_AFTER - HOUR
    linked = await _finding(engine, tenant, started_at=long_ago - DAY, closed_at=long_ago)
    await _active_case(engine, tenant, linked, acted_at=long_ago - DAY, worked=False)
    await _synced_link(engine, tenant, linked)

    outcome = await cases.sweep(engine, now=NOW)

    row = await _row(engine, tenant, linked)
    assert row.case_state == "investigating"
    assert row.acknowledged_at is None
    assert outcome.ended_untouched == 0


async def test_the_idle_warning_asks_the_synced_issue_for_one_comment_in_its_life(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR26, AC97, AC96: one comment request on the synced link, written by the sync job
    later; a second idle spell asks for none, and the sweep itself calls nothing."""
    root = await _finding(engine, tenant, started_at=NOW - 80 * DAY, closed_at=NOW - 79 * DAY)
    warn_at = NOW - (CASE_IDLE_LIMIT - CASE_IDLE_WARNING_AHEAD) - HOUR
    await _active_case(engine, tenant, root, acted_at=warn_at)
    link_id = await _synced_link(engine, tenant, root)

    await cases.sweep(engine, now=NOW)

    [link] = await rows(engine, "SELECT * FROM finding_links WHERE id = :id", id=link_id)
    assert (link.comment_wanted, link.idle_warning_at, link.next_sync_at) == (
        "idle_warning",
        NOW,
        NOW,
    )

    # The comment went; a person acted; a second spell warns, and asks for no comment.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE finding_links SET comment_wanted = NULL WHERE id = :id"), {"id": link_id}
        )
    later = NOW + CASE_IDLE_LIMIT
    await _set(
        engine,
        tenant,
        root,
        case_activity_at=later - (CASE_IDLE_LIMIT - CASE_IDLE_WARNING_AHEAD) - HOUR,
    )
    await cases.sweep(engine, now=later)
    [link] = await rows(engine, "SELECT * FROM finding_links WHERE id = :id", id=link_id)
    assert link.comment_wanted is None
    assert (await _row(engine, tenant, root)).case_warned_at == later


class RecordingEmail:
    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.sent: list[tuple[str, Any, dict[str, Any]]] = []

    async def send(self, *, to: str, message: Any, data: dict[str, Any], **_: Any) -> None:
        from nexovue_platform.email import EmailError

        self.sent.append((to, message, data))
        if self.failing:
            raise EmailError("refused on purpose")


async def _owe(engine: AsyncEngine, tenant: AlertTenant, finding: uuid.UUID, *kinds: str) -> None:
    async with engine.begin() as connection:
        for offset, kind in enumerate(kinds):
            await connection.execute(
                text(
                    "INSERT INTO finding_emails_owed (org_id, finding_id, kind, at) "
                    "VALUES (:org, :finding, :kind, :at)"
                ),
                {"org": tenant.org_id, "finding": finding, "kind": kind, "at": NOW + offset * HOUR},
            )


async def test_two_emails_owed_before_one_sweep_are_both_sent_to_the_assignee(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """AC94, D90: a row per email, deleted before its send; the assignee is told, by
    `scheduler-light`, with no title, note or tracker text."""
    from nexovue_platform.email import Message

    member = await _member(engine, tenant)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET email_verified_at = :now WHERE id = :id"),
            {"now": NOW, "id": member},
        )
    root = await _finding(engine, tenant, started_at=NOW - DAY, closed_at=NOW - HOUR)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR, assignee=member)
    await _owe(engine, tenant, root, "idle_warning", "tracker_closed")
    sender = RecordingEmail()

    await cases.sweep(engine, now=NOW, email_sender=sender, app_base_url="https://n.example")

    (address,) = await rows(engine, "SELECT email FROM users WHERE id = :id", id=member)
    assert [(to, message) for to, message, _ in sender.sent] == [
        (address.email, Message.CASE_IDLE_WARNING),
        (address.email, Message.CASE_TRACKER_CLOSED),
    ]
    for _, _, data in sender.sent:
        assert data["service"] == "checkout"
        assert data["link"] == f"https://n.example/findings/{root}"
    assert (
        await rows(engine, "SELECT id FROM finding_emails_owed WHERE finding_id = :id", id=root)
        == []
    )


async def test_with_nobody_assigned_owners_and_admins_are_told_and_a_failed_send_is_not_repeated(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    from nexovue_platform.email import Message

    admin = await _member(engine, tenant, role="admin")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET email_verified_at = :now WHERE id = :id"),
            {"now": NOW, "id": admin},
        )
    await _member(engine, tenant, role="admin")  # unverified: not told
    root = await _finding(engine, tenant, started_at=NOW - DAY, closed_at=NOW - HOUR)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR)
    await _owe(engine, tenant, root, "tracker_closed_signal_open")
    sender = RecordingEmail(failing=True)

    await cases.sweep(engine, now=NOW, email_sender=sender, app_base_url="https://n.example")
    await cases.sweep(engine, now=NOW, email_sender=sender, app_base_url="https://n.example")

    assert {message for _, message, _ in sender.sent} == {Message.CASE_TRACKER_CLOSED_SIGNAL_OPEN}
    # The owner and the verified admin were each tried once; the refused row is not owed again.
    assert len(sender.sent) == 2
    assert (
        await rows(engine, "SELECT id FROM finding_emails_owed WHERE finding_id = :id", id=root)
        == []
    )


async def test_without_a_mail_sender_owed_emails_wait(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    root = await _finding(engine, tenant, started_at=NOW - DAY, closed_at=NOW - HOUR)
    await _active_case(engine, tenant, root, acted_at=NOW - HOUR)
    await _owe(engine, tenant, root, "tracker_closed")
    await cases.sweep(engine, now=NOW)
    assert (
        len(
            await rows(engine, "SELECT id FROM finding_emails_owed WHERE finding_id = :id", id=root)
        )
        == 1
    )


async def test_a_connection_needing_attention_tells_owners_and_admins_once_per_episode(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR33, AC78: one email across repeated failures; a second only after recovery and
    a new failure; failing tells only after 24 hours; the expiry email is independent."""
    from nexovue_platform.email import Message

    connection_id, _ = await _place(engine, tenant)
    sender = RecordingEmail()

    async def state(**columns: Any) -> None:
        assignments = ", ".join(f"{name} = :{name}" for name in columns)
        async with engine.begin() as connection:
            await connection.execute(
                text(f"UPDATE connections SET {assignments} WHERE id = :id"),
                {"id": connection_id, **columns},
            )

    async def sweep(at: datetime) -> list[Any]:
        sender.sent.clear()
        await cases.sweep(engine, now=at, email_sender=sender, app_base_url="https://n.example")
        return [message for _, message, _ in sender.sent]

    await state(state="needs_attention", last_failure="unauthorized")
    assert await sweep(NOW) == [Message.CONNECTION_NEEDS_ATTENTION]
    assert await sweep(NOW + HOUR) == []
    # Working again clears the marker (the jobs' state machine does); a new episode tells.
    await state(state="working", failure_told_at=None, last_failure=None)
    await state(state="needs_attention", last_failure="app_not_installed")
    assert await sweep(NOW + 2 * HOUR) == [Message.CONNECTION_NEEDS_ATTENTION]

    await state(state="failing", failure_told_at=None, failing_since=NOW, last_failure="timed_out")
    assert await sweep(NOW + 23 * HOUR) == []
    assert await sweep(NOW + 25 * HOUR) == [Message.CONNECTION_FAILING]

    await state(token_expires_on=(NOW + 10 * DAY).date())
    assert await sweep(NOW + 26 * HOUR) == [Message.CONNECTION_EXPIRING]
    assert await sweep(NOW + 27 * HOUR) == []
    [row] = await rows(engine, "SELECT * FROM connections WHERE id = :id", id=connection_id)
    assert row.failure_told_at is not None and row.expiry_told_at is not None


# ------------------------------------------- a repository turned public (T6.2)


async def test_admins_are_told_once_for_each_repository_that_turned_public(
    engine: AsyncEngine, tenant: AlertTenant
) -> None:
    """FR42, AC115: the refresh clears `public_told_at` when a repository turns public;
    one email per such repository then, naming it by its strict key, and never again for
    that turn. A repository first seen public, and a private one, owe nothing."""
    from nexovue_platform.email import Message

    from nexovue_scheduler.case_emails import tell_connection_admins

    connection_id, place_id = await _place(engine, tenant)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connection_repositories (org_id, connection_id, external_id, key, "
                "is_private, public_told_at) VALUES (:org, :connection, '43', 'acme/site', "
                "false, :at), (:org, :connection, '44', 'acme/docs', true, NULL)"
            ),
            {"org": tenant.org_id, "connection": connection_id, "at": NOW - DAY},
        )
        await connection.execute(
            text("UPDATE connection_repositories SET is_private = false WHERE id = :id"),
            {"id": place_id},
        )
    sender = RecordingEmail()

    async def tell(at: datetime) -> list[tuple[Any, dict[str, Any]]]:
        sender.sent.clear()
        await tell_connection_admins(
            engine, sender=sender, app_base_url="https://n.example", now=at
        )
        return [(message, data) for to, message, data in sender.sent if to == tenant.owner_email]

    assert await tell(NOW) == [
        (
            Message.CONNECTION_REPOSITORY_PUBLIC,
            {
                "tracker": "GitHub",
                "repository": "acme/api",
                "link": "https://n.example/settings/connections",
            },
        )
    ]
    assert await tell(NOW + HOUR) == []
    [told] = await rows(
        engine, "SELECT public_told_at FROM connection_repositories WHERE id = :id", id=place_id
    )
    assert told.public_told_at == NOW
