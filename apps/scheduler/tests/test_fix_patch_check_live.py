"""The check run for a patch, against the simulated GitHub (F5 plan T5.1; spec FR20a, FR21,
AC10a's patch half, AC19, AC24, AC28a, AC29a's check half, AC40, AC55).

A written suggestion is made by the AI job's own pure code (`proposal.stored_edits`) from
the file the fake holds, as the job would have stored it. The check re-reads the default
branch's head, re-applies the edits and runs the rules again: **unchanged** or **moved**
stores the patch on the person's own check row; **out of date** moves the suggestion to
`stale`, with no alert; **refused again** (a row tampered at the same rules and files) ends
it `failed`, `checks_refused_at_click`, deletes its code and audits it. **No call writes
to GitHub** (AC28a). Every test builds and drops its own organization; the clock is its own.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id, patch_hunks
from nexovue_platform.fixes.pack import owner_lists_hash
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.records import FIX_CHECKS_REFUSED, PATCH_CHECK_LIFETIME
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    build_world,
    consent,
    drop_world,
    fix_row,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_check

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_patch_check_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

FOLDER: Final = "services/shop"
PATH: Final = f"{FOLDER}/app/pay.py"
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
# A plain reason: with no explanation, a stand-in-shaped token would fail FR16 at read.
WHY: Final = "The call needs a retry count."


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
    fake.seed_files(REPOSITORY, {PATH: READ, f"{FOLDER}/app/other.py": "x = 1\n"})
    try:
        yield made
    finally:
        await drop_world(engine, made)


def _edits(new: str = NEW) -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, new, WHY, ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


async def _proposed(
    engine: AsyncEngine,
    world: SourceWorld,
    now: datetime,
    *,
    edits: list[dict[str, Any]] | None = None,
    **columns: Any,
) -> uuid.UUID:
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
    values: dict[str, Any] = {
        "edits": json.dumps(_edits() if edits is None else edits),
        "files_read": json.dumps(files_read),
        "checks_version": CHECKS_VERSION,
        "extra_list_hash": owner_lists_hash(OwnerLists()),
        "finished_at": now,
        "result_recorded_at": now,
    }
    values.update(columns)
    return await source_fix(engine, world, candidates=[], now=now, state="proposed", **values)


async def _check(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime, *, user: Any = None
) -> str:
    async with engine.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO finding_fix_checks (org_id, fix_id, requested_by, purpose, status,
                    requested_at, expires_at)
                VALUES (:org, :fix, :user, 'patch', 'running', :now, :expires)
                RETURNING id
                """
            ),
            {
                "org": world.org_id,
                "fix": fix_id,
                "user": user or world.user_id,
                "now": now,
                "expires": now + timedelta(minutes=15),
            },
        )
    return str(made)


async def _run(engine: AsyncEngine, world: SourceWorld, check_id: str, now: datetime) -> Any:
    return await fix_check.check_fix(
        engine,
        org_id=str(world.org_id),
        check_id=check_id,
        keys=KEYS,
        endpoints=world.endpoints(),
        go=None,
        show_held_causes=False,
        now=now,
    )


async def _check_row(engine: AsyncEngine, check_id: str) -> Any | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT * FROM finding_fix_checks WHERE id = :id"), {"id": check_id}
            )
        ).one_or_none()


async def _audit(engine: AsyncEngine, world: SourceWorld, action: str) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT actor_user_id, detail FROM audit_log "
                    "WHERE org_id = :org AND action = :action ORDER BY at"
                ),
                {"org": world.org_id, "action": action},
            )
        )


def _writes(fake: FakeGitHub) -> list[Any]:
    """Any call that could change a repository (AC28a): only token minting may POST."""
    return [
        call
        for call in fake.calls()
        if call.method not in ("GET", "HEAD")
        and not call.path.endswith("/access_tokens")
        and "/installation/token" not in call.path
    ]


async def test_ac28a_an_unmoved_suggestion_stores_its_patch_on_the_person_s_own_check(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "unchanged"
    assert await _run(engine, world, check, now) is None

    row = await _check_row(engine, check)
    assert row is not None and row.status == "unchanged" and row.reason is None
    assert row.expires_at == now + PATCH_CHECK_LIFETIME
    assert row.blob_ids == {PATH: git_blob_id(READ)} and row.moved_refs == []
    [hunks] = patch_hunks(row.patch)
    assert [line for marker, line in hunks[0].lines if marker != " "] == [OLD, NEW]
    assert len(row.digest) == 64 and len(row.head_sha) == 40
    assert (await fix_row(engine, world, fix_id)).state == "proposed"
    [read] = await _audit(engine, world, "repository.read")
    assert read.actor_user_id == world.user_id
    assert read.detail["purpose"] == "patch" and read.detail["paths"][0]["path"] == PATH
    assert _writes(fake) == []


async def test_ac24_a_file_moved_on_the_default_branch_whose_edit_applies_is_moved(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    moved = "# changed since\n" + READ
    fake.seed_files(REPOSITORY, {PATH: moved})
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "moved"

    row = await _check_row(engine, check)
    assert row is not None and row.moved_refs == ["file_1"]
    assert row.blob_ids == {PATH: git_blob_id(moved)}
    assert patch_hunks(row.patch)[0][0].old_start == 5
    assert (await fix_row(engine, world, fix_id)).state == "proposed"


async def test_ac24_a_moved_file_whose_edit_no_longer_applies_is_stale_with_no_alert(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    fake.seed_files(REPOSITORY, {PATH: READ.replace(OLD, "    return gateway.pay(amount)\n")})
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "stale"

    row = await _check_row(engine, check)
    assert row is not None and (row.status, row.reason, row.patch) == (
        "stale",
        "edit_no_longer_applies",
        None,
    )
    stale = await fix_row(engine, world, fix_id)
    assert (stale.state, stale.reason) == ("stale", "edit_no_longer_applies")
    assert stale.edits is not None  # a stale suggestion keeps its code (D50)
    assert await _audit(engine, world, FIX_CHECKS_REFUSED) == []


async def test_a_changed_file_past_the_client_s_cap_is_out_of_date(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await _proposed(engine, world, now)
    fake.seed_files(REPOSITORY, {PATH: READ + "# " + "x" * (900 * 1024) + "\n"})
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "stale"
    assert (await _check_row(engine, check)).reason == "edit_no_longer_applies"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"checks_version": "2026-01-01.0"}, "rules_changed"),
        ({"extra_list_hash": "0" * 64}, "rules_changed"),
        ({"folder": f"{FOLDER}/app"}, "mapping_changed"),
    ],
)
async def test_ac55_changed_rules_list_or_location_are_stale_before_any_file_is_read(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    change: dict[str, Any],
    reason: str,
) -> None:
    fix_id = await _proposed(engine, world, now, **change)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "stale"

    assert (await fix_row(engine, world, fix_id)).reason == reason
    assert fake.calls(f"/repos/{REPOSITORY}/git/blobs") == []
    assert await _audit(engine, world, FIX_CHECKS_REFUSED) == []


async def test_ac55_a_row_tampered_at_the_same_rules_and_files_is_refused_audited_and_cleared(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """K6 needs the file, so only a check run can see this one (an added address)."""
    tampered = _edits(new="    return gateway.charge(amount, to='https://evil.example')\n")
    fix_id = await _proposed(engine, world, now, edits=tampered)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "refused"

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.reason) == ("failed", "checks_refused_at_click")
    assert row.edits is None and row.files_read is None
    assert row.code_deleted_reason == "ended_short"
    assert await _check_row(engine, check) is None  # every check row is code (BR9)
    [audited] = await _audit(engine, world, FIX_CHECKS_REFUSED)
    assert audited.detail["at"] == "check_run" and audited.detail["failure"] == "K6"
    assert "evil" not in json.dumps(audited.detail)


async def test_ac19_a_tampered_display_fact_is_refused_at_the_check(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    edits = _edits()
    edits[0]["last_line_suffix"] = "  # hidden\n"
    fix_id = await _proposed(engine, world, now, edits=edits)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "refused"
    assert (await fix_row(engine, world, fix_id)).reason == "checks_refused_at_click"


async def test_ac10a_under_the_owner_s_requirement_an_unlinked_person_is_refused_before_reading(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE fix_source_consents SET require_github_access = true WHERE org_id = :o"),
            {"o": world.org_id},
        )
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "refused"

    row = await _check_row(engine, check)
    assert row is not None and (row.reason, row.patch) == ("not_linked", None)
    assert fake.calls(f"/repos/{REPOSITORY}/git/blobs") == []
    assert (await fix_row(engine, world, fix_id)).state == "proposed"


async def test_ac10a_under_the_owner_s_requirement_a_person_github_says_can_read_gets_a_patch(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE fix_source_consents SET require_github_access = true WHERE org_id = :o"),
            {"o": world.org_id},
        )
        await connection.execute(
            text(
                "INSERT INTO github_user_links (user_id, org_id, github_user_id, github_login) "
                "VALUES (:user, :org, 9201, 'reader')"
            ),
            {"user": world.user_id, "org": world.org_id},
        )
    fake.add_user(9201, "reader")
    fake.grant(REPOSITORY, "reader", "read")
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "unchanged"


async def test_ac29a_a_withdrawal_before_the_store_leaves_no_patch(
    engine: AsyncEngine, world: SourceWorld, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The withdrawal commits between the read and the store: nothing is stored, and no
    column of the check holds the file's text."""
    from nexovue_scheduler.connections import fix_patch

    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)
    real = fix_patch.audit_read

    async def withdraw_then_audit(*args: Any, **kwargs: Any) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE fix_source_consents SET withdrawn_at = :now, withdrawn_reason = "
                    "'owner' WHERE org_id = :org"
                ),
                {"now": now, "org": world.org_id},
            )
        await real(*args, **kwargs)

    monkeypatch.setattr(fix_patch, "audit_read", withdraw_then_audit)

    assert await _run(engine, world, check, now) == "failed"

    row = await _check_row(engine, check)
    assert row is not None and (row.reason, row.patch, row.digest) == (
        "consent_withdrawn",
        None,
        None,
    )
    assert "gateway" not in json.dumps([str(value) for value in row])


async def test_ac40_one_person_s_patch_check_does_not_touch_another_s(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    async with engine.begin() as connection:
        other = await connection.scalar(
            text(
                """
                INSERT INTO users (org_id, email, name, role, password_hash)
                VALUES (:org, :email, 'Bea', 'member', 'x')
                RETURNING id
                """
            ),
            {"org": world.org_id, "email": f"bea-{uuid.uuid4()}@example.test"},
        )
    fix_id = await _proposed(engine, world, now)
    mine = await _check(engine, world, fix_id, now)
    theirs = await _check(engine, world, fix_id, now, user=other)

    assert await _run(engine, world, mine, now) == "unchanged"

    assert (await _check_row(engine, theirs)).status == "running"  # type: ignore[union-attr]


async def test_consent_ended_by_itself_does_not_stop_a_patch(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    """D43: nothing goes to a provider at a check run, so a changed model does not stop it."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE ai_connections SET model = 'model-b' WHERE org_id = :o"),
            {"o": world.org_id},
        )
    await consent(engine, world.org_id, world.repository_id, folder=f"{FOLDER}/x", now=now)
    fix_id = await _proposed(engine, world, now)
    check = await _check(engine, world, fix_id, now)

    assert await _run(engine, world, check, now) == "unchanged"
