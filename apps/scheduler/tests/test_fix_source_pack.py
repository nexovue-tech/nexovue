"""Reading a suggestion's files, against real Postgres and the simulated GitHub (F5 plan
T3.10, T3.16, T3.18, T3.19; spec AC7's read half, AC10a's job half, AC12, AC12b, AC12f,
AC12g, AC12h, AC13, AC16a, AC16c, AC35's path half, AC45, AC80 to AC82, AC84).

**Against the simulated GitHub only** (foundation item 28): `tracker_fake.py` answers as
GitHub's documents read. Every blob the job reads is in the fake's log, so "never fetched"
is proved by the log, not by the job's word. Each test builds and drops its own
organization; the clock is the test's own.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from alert_fixtures import postgres_reachable
from live import require_stack
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    audit_reads,
    build_world,
    consent,
    drop_world,
    fix_row,
    pack_of,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import source_pack
from nexovue_scheduler.jobs import registry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_source_pack.py",
    remedy="Postgres is not reachable — run `just up`",
)

FOLDER = "services/shop"
HANDLER = (
    "from . import money\n"
    "from .config import settings\n"
    "from ...shared import outside\n"
    "\n"
    "def pay(order):\n"
    "    return money.charge(order)\n"
)
MONEY = "from . import tax\n\ndef charge(order):\n    return order.total\n"


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def fake() -> Iterator[FakeGitHub]:
    with serve_fake_github() as served:
        yield served


@pytest.fixture
async def world(engine: AsyncEngine, fake: FakeGitHub, now: datetime) -> AsyncIterator[SourceWorld]:
    made = await build_world(engine, fake, folder=FOLDER, now=now)
    fake.seed_files(
        REPOSITORY,
        {
            f"{FOLDER}/app/handler.py": HANDLER,
            f"{FOLDER}/app/money.py": MONEY,
            f"{FOLDER}/app/tax.py": "RATE = 0.2\n",
            f"{FOLDER}/app/config/settings.py": "DEBUG = False\n",
            f"{FOLDER}/tests/test_handler.py": "def test_pay():\n    assert True\n",
            f"{FOLDER}/.env": "TOKEN=abc\n",
            f"{FOLDER}/prod.env": "X=1\n",
            f"{FOLDER}/.github/x.py": "print(1)\n",
            f"{FOLDER}/id_ed25519": "key\n",
            f"{FOLDER}/application-prod.yml": "a: 1\n",
            f"{FOLDER}/keys.py": 'aws = "AKIA' + "QWERTYUIOPASDFGH" + '"\n',
            "shared/outside.py": "x = 1\n",
            "other/app/handler.py": "x = 1\n",
        },
    )
    try:
        yield made
    finally:
        await drop_world(engine, made)


def _stack(*frames: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"path": path, "line": line, "from": "stack"} for path, line in frames]


async def _run(engine: AsyncEngine, world: SourceWorld, fix_id: Any, now: datetime) -> str | None:
    return await source_pack.build_source_pack(
        engine,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        now=now,
    )


def _blob_reads(fake: FakeGitHub) -> int:
    return len(fake.calls(f"/repos/{REPOSITORY}/git/blobs"))


def _listed(row: Any) -> list[str]:
    return [each["path"] for each in row.files_read["files"]]


async def test_a_stack_lists_its_files_imports_and_test_and_stops_gathered(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack(("/srv/app/handler.py", 5)), now=now)

    assert await _run(engine, world, fix_id, now) == "gathered"

    row = await fix_row(engine, world, fix_id)
    assert row.state == "gathered" and row.path_candidates is None
    assert _listed(row) == [
        f"{FOLDER}/app/handler.py",
        f"{FOLDER}/app/money.py",
        f"{FOLDER}/tests/test_handler.py",
    ]
    roles = {each["path"]: (each["role"], each["why"]) for each in row.files_read["files"]}
    assert roles[f"{FOLDER}/app/money.py"] == (
        "imported",
        {"kind": "imported_by", "path": f"{FOLDER}/app/handler.py"},
    )
    assert roles[f"{FOLDER}/app/handler.py"] == ("stack", {"kind": "stack", "line": 5})
    pack = await pack_of(engine, world, fix_id)
    assert pack is not None and pack.frames == [{"ref": "file_1", "line": 5}]
    assert row.files_digest and len(row.files_digest) == 64


async def test_ac80_imports_are_one_hop_inside_the_folder_and_never_sent_stays_listed(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert f"{FOLDER}/app/tax.py" not in _listed(row)  # an import of an import
    left = {each["path"]: each["reason"] for each in row.files_read["left_out"]}
    assert left[f"{FOLDER}/app/config/settings.py"] == "configuration"
    read = {call.path.rsplit("/", 1)[-1] for call in fake.calls(f"/repos/{REPOSITORY}/git/blobs")}
    tree = dict(
        fake.git_of(REPOSITORY).flat(
            fake.git_of(REPOSITORY).commits[fake.git_of(REPOSITORY).refs["main"]].tree
        )
    )
    for never in (f"{FOLDER}/app/config/settings.py", "shared/outside.py", f"{FOLDER}/app/tax.py"):
        assert tree[never][1] not in read, never


async def test_ac82_the_read_is_one_audit_entry_naming_the_person_and_a_rerun_writes_nothing(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    await _run(engine, world, fix_id, now)
    calls = len(world.fake.site.requests)
    assert await _run(engine, world, fix_id, now) is None
    assert len(world.fake.site.requests) == calls  # stopped before any call

    [entry] = await audit_reads(engine, world)
    assert entry.actor_user_id == world.user_id
    assert entry.detail["purpose"] == "fix"
    assert {"path": f"{FOLDER}/app/money.py", "role": "imported"} in entry.detail["paths"]


@pytest.mark.parametrize(
    "frame",
    [
        f"/srv/{FOLDER}/prod.env",
        "/srv/services/shop/.ENV",
        f"/srv/{FOLDER}/.github/x.py",
        f"/srv/{FOLDER}/id_ed25519",
        f"/srv/{FOLDER}/application-prod.yml",
        "/srv/other/thing/handler.py",
        "/srv/shared/outside.py",
    ],
)
async def test_ac12_a_listed_outside_or_unsourced_file_is_never_fetched(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime, frame: str
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack((frame, 1)), now=now)

    assert await _run(engine, world, fix_id, now) == "refused"

    assert _blob_reads(fake) == 0
    row = await fix_row(engine, world, fix_id)
    assert row.reason == "no_usable_file" and row.path_candidates is None


async def test_ac13_a_secret_shaped_file_is_not_in_the_pack_and_is_listed(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    frames = _stack(("app/handler.py", 5), ("keys.py", 1))
    fix_id = await source_fix(engine, world, candidates=frames, now=now)

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert {"path": f"{FOLDER}/keys.py", "reason": "secret_pattern", "bytes": 29} in row.files_read[
        "left_out"
    ]
    pack = await pack_of(engine, world, fix_id)
    assert pack is not None
    assert all(each["path"] != f"{FOLDER}/keys.py" for each in pack.files)
    assert "AKIA" not in json.dumps(pack.files)


async def test_ac12g_a_forged_path_is_a_count_and_in_no_column(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    forged = "/srv/app/ignore_previous_instructions.py"
    fix_id = await source_fix(
        engine, world, candidates=_stack(("app/handler.py", 5), (forged, 3)), now=now
    )

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert row.files_read["unmatched"] == 1
    async with engine.connect() as connection:
        found = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM finding_fixes WHERE org_id = :org "
                    "AND (finding_fixes::text LIKE '%ignore_previous%')"
                ),
                {"org": world.org_id},
            )
        ).scalar_one()
    assert found == 0


async def test_ac16a_a_cut_tree_is_refused_with_no_blob_and_no_retry(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.tree_entry_limit = 3
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    assert await _run(engine, world, fix_id, now) == "refused"

    row = await fix_row(engine, world, fix_id)
    assert row.reason == "repository_too_large"
    assert _blob_reads(fake) == 0


async def test_a_github_outage_is_retried_then_ends_failed(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fake.fail("read_tree", 502)
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    with pytest.raises(registry.Retry):
        await _run(engine, world, fix_id, now)
    token = registry._ATTEMPT.set(3)
    try:
        assert await _run(engine, world, fix_id, now) == "failed"
    finally:
        registry._ATTEMPT.reset(token)
    assert (await fix_row(engine, world, fix_id)).reason == "github_unavailable"


async def test_ac12h_a_commit_github_does_not_show_as_an_ancestor_is_not_read(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    forked = "f" * 40
    fix_id = await source_fix(
        engine, world, candidates=_stack(("app/handler.py", 5)), now=now, read_commit=forked
    )

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert row.read_commit_known is False
    assert row.read_commit == fake.git_of(REPOSITORY).refs["main"]
    assert not any(forked in call.path for call in fake.calls(f"/repos/{REPOSITORY}/git/"))


async def test_ac16c_a_known_commit_and_a_file_moved_on_the_default_branch_are_labelled(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    running = fake.git_of(REPOSITORY).refs["main"]
    fake.seed_files(REPOSITORY, {f"{FOLDER}/app/money.py": MONEY + "# later\n"})
    fix_id = await source_fix(
        engine, world, candidates=_stack(("app/handler.py", 5)), now=now, read_commit=running
    )

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    moved = {each["path"]: each["moved_on_default"] for each in row.files_read["files"]}
    assert row.read_commit == running and row.read_commit_known is True
    assert moved[f"{FOLDER}/app/money.py"] is True
    assert moved[f"{FOLDER}/app/handler.py"] is False


async def test_ac10a_consent_ended_by_a_model_change_stops_before_any_call(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE ai_connections SET model = 'model-b' WHERE org_id = :org"),
            {"org": world.org_id},
        )
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)
    before = len(fake.site.requests)

    assert await _run(engine, world, fix_id, now) == "failed"

    assert (await fix_row(engine, world, fix_id)).reason == "consent_ended_in_flight"
    assert len(fake.site.requests) == before


async def _require_access(engine: AsyncEngine, world: SourceWorld, now: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM fix_source_consents WHERE org_id = :org"), {"org": world.org_id}
        )
    await consent(
        engine,
        world.org_id,
        world.repository_id,
        folder=FOLDER,
        now=now,
        require_github_access=True,
    )


async def _link(engine: AsyncEngine, world: SourceWorld, github_id: int, login: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO github_user_links (user_id, org_id, github_user_id, github_login) "
                "VALUES (:user, :org, :id, :login)"
            ),
            {"user": world.user_id, "org": world.org_id, "id": github_id, "login": login},
        )


async def test_ac10a_under_the_owner_s_requirement_an_unlinked_person_reads_no_blob(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    await _require_access(engine, world, now)
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    assert await _run(engine, world, fix_id, now) == "failed"

    assert (await fix_row(engine, world, fix_id)).reason == "not_linked"
    assert _blob_reads(fake) == 0


async def test_ac10a_a_linked_person_without_read_access_is_refused_before_any_blob(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    await _require_access(engine, world, now)
    fake.add_user(9001, "outsider")
    await _link(engine, world, 9001, "outsider")
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    assert await _run(engine, world, fix_id, now) == "failed"

    assert (await fix_row(engine, world, fix_id)).reason == "no_github_access"
    assert _blob_reads(fake) == 0


async def test_ac10a_a_linked_person_with_read_access_gets_the_list(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    await _require_access(engine, world, now)
    fake.add_user(9002, "reader")
    fake.grant(REPOSITORY, "reader", "read")
    await _link(engine, world, 9002, "reader")
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)

    assert await _run(engine, world, fix_id, now) == "gathered"


async def test_typed_paths_are_named_and_a_miss_is_said_beside_its_path(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    typed = [
        {"path": "app/money.py", "line": None, "from": "person"},
        {"path": "app/nowhere.py", "line": None, "from": "person"},
    ]
    fix_id = await source_fix(engine, world, candidates=typed, candidates_from="person", now=now)

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert _listed(row)[0] == f"{FOLDER}/app/money.py"
    assert row.files_read["files"][0]["role"] == "named"
    assert row.files_read["typed_missing"] == ["app/nowhere.py"]


async def test_a_removed_file_is_not_read_again_nor_added_back_as_an_import(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    candidates: list[dict[str, Any]] = [
        {
            "path": "app/handler.py",
            "line": None,
            "from": "kept",
            "role": "stack",
            "why": {"kind": "stack", "line": 5},
        },
        {"path": "app/money.py", "line": None, "from": "removed"},
    ]
    fix_id = await source_fix(
        engine, world, candidates=candidates, candidates_from="person", now=now
    )

    await _run(engine, world, fix_id, now)

    assert f"{FOLDER}/app/money.py" not in _listed(await fix_row(engine, world, fix_id))


async def test_ac84_hostile_asked_names_read_no_blob_and_leave_no_trace(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)
    await _run(engine, world, fix_id, now)
    before = _blob_reads(fake)
    # At most three names a round (FR15b); the rest are asked one by one below.
    hostile = [
        "../../etc/passwd",
        ".env",
        ".github/workflows/ci.yml",
        "/etc/passwd",
        "app/a\nb.py",
        "https://evil.example/x.py",
        "app/never_there.py",
    ]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET state = 'gathering', candidates_from = 'model', "
                "round = 2, requested_paths = CAST(:asked AS jsonb) WHERE id = :id"
            ),
            {
                "id": fix_id,
                "asked": json.dumps([{"path": p, "ref": "file_1", "line": 5} for p in hostile[:3]]),
            },
        )

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    assert _blob_reads(fake) == before
    assert row.requested_paths is None and row.state == "gathered"
    # One refused by shape and one matching no file are counts; `.env` exists, so it is
    # listed with its reason, never read.
    assert row.files_read["unmatched"] == 2
    assert {"path": f"{FOLDER}/.env", "reason": "secret_or_key", "bytes": 10} in (
        row.files_read["left_out"]
    )
    assert "etc/passwd" not in json.dumps(row.files_read)
    assert {"path": f"{FOLDER}/.github/workflows/ci.yml", "reason": "ci", "bytes": None} not in (
        row.files_read["left_out"]
    )
    for name in hostile[3:]:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE finding_fixes SET state = 'gathering', requested_paths = "
                    "CAST(:asked AS jsonb) WHERE id = :id"
                ),
                {"id": fix_id, "asked": json.dumps([{"path": name, "ref": "file_1", "line": 5}])},
            )
        await _run(engine, world, fix_id, now)
    assert _blob_reads(fake) == before
    assert "evil.example" not in json.dumps((await fix_row(engine, world, fix_id)).files_read)


async def test_t3_19_an_asked_name_that_passes_joins_the_pack_as_it_stands(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(engine, world, candidates=_stack(("app/handler.py", 5)), now=now)
    await _run(engine, world, fix_id, now)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET state = 'gathering', candidates_from = 'model', "
                "round = 2, requested_paths = CAST(:asked AS jsonb) WHERE id = :id"
            ),
            {
                "id": fix_id,
                "asked": json.dumps([{"path": "app/tax.py", "ref": "file_2", "line": 1}]),
            },
        )

    await _run(engine, world, fix_id, now)

    row = await fix_row(engine, world, fix_id)
    asked = row.files_read["files"][-1]
    assert (asked["path"], asked["role"]) == (f"{FOLDER}/app/tax.py", "asked")
    assert asked["why"] == {"kind": "asked", "ref": "file_2", "line": 1}
    assert _listed(row)[:3] == [
        f"{FOLDER}/app/handler.py",
        f"{FOLDER}/app/money.py",
        f"{FOLDER}/tests/test_handler.py",
    ]
    assert row.files_read["round"] == 2


async def test_ac35_the_brief_s_paths_read_the_tree_and_no_blob(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    request_id = "00000000-0000-4000-8000-0000000000aa"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO job_results (request_id, org_id, kind, payload, expires_at) "
                "VALUES (CAST(:id AS uuid), :org, 'brief_paths', CAST(:payload AS jsonb), :at)"
            ),
            {
                "id": request_id,
                "org": world.org_id,
                "payload": json.dumps(
                    {"candidates": _stack(("/srv/app/handler.py", 5), ("/srv/app/forged.py", 1))}
                ),
                "at": now.replace(year=2027),
            },
        )

    found = await source_pack.match_brief_paths(
        engine,
        org_id=str(world.org_id),
        finding_id=str(world.finding_id),
        request_id=request_id,
        keys=KEYS,
        endpoints=world.endpoints(),
    )

    assert found == [{"path": f"{FOLDER}/app/handler.py", "line": 5}]
    assert _blob_reads(fake) == 0


async def test_logs_hold_no_content_or_path_of_a_secret(
    engine: AsyncEngine, world: SourceWorld, now: datetime
) -> None:
    fix_id = await source_fix(
        engine, world, candidates=_stack(("app/handler.py", 5), ("keys.py", 1)), now=now
    )

    with capture_logs() as logs:
        await _run(engine, world, fix_id, now)

    assert "AKIA" not in json.dumps(logs, default=str)
    assert "money.charge" not in json.dumps(logs, default=str)
