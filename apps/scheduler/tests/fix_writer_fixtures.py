"""The draft job's shared world, for the suites that drive F5's write path against the
simulated GitHub (plan T7.5, T8.1, T8.2): a tenant with the Fix App, a seeded repository, a
linked person with write access, drafts allowed and a signed-deploy cause; a written
suggestion, the person's own `draft` check, the click's approval, and the draft job.

**The go here is built only for tests** (`TEST_GO`); the product passes
`write_gate.WRITE_ACCESS_GO`, which is None (ADR-097). Each world builds and drops its own
organization; the clock is the test's own, read when it starts.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import pytest
from github_git_data import GitData
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id
from nexovue_platform.fixes.pack import owner_lists_hash
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.write_gate import WriteGo
from source_fix_fixtures import (
    KEYS,
    REPOSITORY,
    SourceWorld,
    build_world,
    drop_world,
    source_fix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import fix_check, fix_writer

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
WRITE_METHODS: Final = frozenset({"POST", "PATCH", "DELETE", "PUT"})


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
async def world(
    engine: AsyncEngine, connections_engine: AsyncEngine, fake: FakeGitHub, now: datetime
) -> AsyncIterator[SourceWorld]:
    made = await build_world(engine, fake, jobs=connections_engine, folder=FOLDER, now=now)
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


def written_edits() -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, NEW, WHY, ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


async def proposed_fix(
    engine: AsyncEngine,
    world: SourceWorld,
    now: datetime,
    *,
    edits: list[dict[str, Any]] | None = None,
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
    return await source_fix(
        engine,
        world,
        candidates=[],
        now=now,
        state="proposed",
        edits=json.dumps(written_edits() if edits is None else edits),
        files_read=json.dumps(files_read),
        checks_version=CHECKS_VERSION,
        extra_list_hash=owner_lists_hash(OwnerLists()),
        explanation_version=1,
        finished_at=now,
        result_recorded_at=now,
    )


async def draft_check(
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
        world.jobs,
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


async def approve(
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


async def approved_fix(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    """A suggestion approved from the person's own passed check, as the click leaves it."""
    fix_id = await proposed_fix(engine, world, now)
    status, check = await draft_check(engine, world, fix_id, now, go=TEST_GO)
    assert status == "unchanged", (status, check.reason)
    await approve(engine, world, fix_id, check.digest, now)
    return fix_id


async def open_draft(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime, *, go: Any = TEST_GO
) -> str | None:
    return await fix_writer.open_pull_request(
        world.jobs,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=go,
        show_held_causes=True,
        base_url=BASE_URL,
        now=now,
    )


def git_of(fake: FakeGitHub) -> GitData:
    return fake.git_of(REPOSITORY)


def writes_seen(fake: FakeGitHub) -> list[str]:
    """Every request that could change a repository, from the fake's own log: a write
    method on a repository route, or a token minted to write."""
    found = []
    for request in fake.site.requests:
        if request.method in WRITE_METHODS and request.path.startswith("/repos/"):
            found.append(f"{request.method} {request.path}")
        if request.path.endswith("/access_tokens") and b'"write"' in request.body:
            found.append(f"write token {request.path}")
    return found


async def audit_rows(engine: AsyncEngine, world: SourceWorld, action: str) -> list[Any]:
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


def repository_writes(fake: FakeGitHub) -> list[str]:
    """Every request that changed, or could change, a repository: a write method on a
    repository route. A write token minted and never used writes nothing."""
    return [
        f"{request.method} {request.path}"
        for request in fake.site.requests
        if request.method in WRITE_METHODS and request.path.startswith("/repos/")
    ]


async def opened_fix(engine: AsyncEngine, world: SourceWorld, now: datetime) -> uuid.UUID:
    """A draft opened by the draft job, with the gate forced on, as Step 7 leaves it."""
    fix_id = await approved_fix(engine, world, now)
    assert await open_draft(engine, world, fix_id, now) == "opened"
    return fix_id


async def ask_ci(engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, at: datetime) -> None:
    """The Run CI click, as the route stores it: who and when, any earlier failure gone."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_fixes SET ci_requested_by = :user, ci_requested_at = :at, "
                "last_write_failure = NULL WHERE org_id = :org AND id = :id"
            ),
            {"user": world.user_id, "at": at, "org": world.org_id, "id": fix_id},
        )


async def run_ci(
    engine: AsyncEngine, world: SourceWorld, fix_id: uuid.UUID, now: datetime, *, go: Any = TEST_GO
) -> str | None:
    return await fix_writer.run_ci(
        world.jobs,
        org_id=str(world.org_id),
        fix_id=str(fix_id),
        keys=KEYS,
        endpoints=world.endpoints(),
        go=go,
        now=now,
    )
