"""The draft pull request's routes and read, against real Postgres (F5 plan T7.4, T7.7's routes,
T7.8's read; spec FR10c, FR22, FR24, FR24a, FR28, BR8, BR14, AC23, AC25, AC32a, AC47, AC48,
AC60, AC61, AC62).

**Two gates, both proved here.** As shipped, `write_gate.WRITE_ACCESS_GO` is None: the draft
check, the click and **Delete this branch** answer 409 `writes_off` with its sentence, add no
job and change no row, and the read hides the action with that sentence in its place. Only
inside a test is the gate forced on (`monkeypatch` on the module's constant, which the routes
read at call time), to prove the path behind it.

Each test builds its own tenant inside a transaction rolled back afterwards, so no job it
adds is ever committed for the stack's workers; the clock is the suite's own `now`. **No
GitHub is reached:** the routes add jobs, with ids only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Final

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id
from nexovue_platform.fixes.policy import CHECKS_VERSION
from nexovue_platform.fixes.proposal import stored_edits
from nexovue_platform.fixes.recheck import CurrentFile, Rechecked, recheck
from nexovue_platform.fixes.records import (
    CHECK_FIX_TASK,
    FIX_PULL_REQUEST_ATTEMPTED,
    FIX_REPOSITORY_SETTINGS_CHANGED,
    OPEN_FIX_PULL_REQUEST_TASK,
    fix_branch,
)
from nexovue_platform.fixes.sentences import (
    CHECK_EXPIRED,
    CI_HELD_ON_PAGE,
    CONFIRM_AUTHOR,
    CONFIRM_CASE,
    CONFIRM_CI_ON_OPEN,
    CONFIRM_HOLDS,
    CONFIRM_LOGIN,
    CONFIRM_REACH,
    DRAFT_REFUSAL_SENTENCES,
    GENERAL_HAS_NO_CODE,
    NOT_YOUR_CHECK,
    OPEN_DRAFT,
    OPENING_HEADING,
    OPENING_REQUESTER,
    WRITE_FAILURE_SENTENCES,
)
from nexovue_platform.fixes.write_gate import WriteGo
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AuditLogEntry,
    Connection,
    ConnectionRepository,
    FindingFix,
    FindingFixCheck,
    FixRepositorySettings,
    GitHubUserLink,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.findings import fix_consent

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_draft_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

# A go built only here, to force the gate on inside a test. The product never builds one.
TEST_GO: Final = WriteGo(
    given_on=date(2027, 1, 4),
    safety_gates_passed_on=("a test's own option",),
    buyers_answer="a test's own record",
    terms="a test's own terms",
    drafts_from_stack_alone=False,
    enterprise_server_before_step_7=False,
)
WRITES_OFF: Final = DRAFT_REFUSAL_SENTENCES["writes_off"]
PATH: Final = f"{source.FOLDER}/app/pay.py"
READ: Final = "def charge(order, gateway):\n    return gateway.charge(order.total)\n"
OLD: Final = "    return gateway.charge(order.total)\n"
NEW: Final = "    return gateway.charge(order.total, retries=2)\n"
WRITE_GRANT: Final = {"contents": "write", "metadata": "read", "pull_requests": "write"}


@pytest.fixture
def gate_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The founder's go, inside this test only (ADR-097): the routes read the constant."""
    monkeypatch.setattr(write_gate, "WRITE_ACCESS_GO", TEST_GO)


def _edits() -> list[dict[str, Any]]:
    edit = Edit("file_1", OLD, NEW, "The call needs a retry count.", ("file_1",))
    return list(stored_edits([edit], {"file_1": PackFile("file_1", PATH, READ)}))


def _files_read() -> dict[str, Any]:
    return {
        "commit": "c" * 40,
        "commit_known": True,
        "files": [
            {
                "ref": "file_1",
                "path": PATH,
                "blob_sha": git_blob_id(READ),
                "mode": "100644",
                "role": "stack",
                "why": {"kind": "stack", "line": 2},
                "bytes": len(READ),
                "lines_sent": None,
                "moved_on_default": False,
            }
        ],
        "left_out": [],
    }


def _rechecked() -> Rechecked:
    outcome = recheck(
        _edits(),
        _files_read(),
        folder=source.FOLDER,
        allowed_evidence={"file_1"},
        aliases=None,
        head={"file_1": CurrentFile(git_blob_id(READ), "100644", READ)},
        base={},
    )
    assert isinstance(outcome, Rechecked)
    return outcome


async def _ready(
    db: AsyncSession,
    now: datetime,
    *,
    allowed: bool = True,
    granted: dict[str, str] | None = None,
    linked: bool = True,
) -> tuple[Any, FindingRow, ConnectionRepository]:
    tenant, finding, repository = await source.ready(db, now)
    app = await db.scalar(
        select(Connection)
        .where(Connection.org_id == tenant.organization.id)
        .where(Connection.kind == "github_fix")
    )
    assert app is not None
    app.granted_permissions = WRITE_GRANT if granted is None else granted
    if allowed:
        db.add(
            FixRepositorySettings(
                org_id=tenant.organization.id,
                connection_repository_id=repository.id,
                drafts_allowed=True,
                set_at=now,
            )
        )
    if linked:
        db.add(
            GitHubUserLink(
                user_id=tenant.user.id,
                org_id=tenant.organization.id,
                github_user_id=9301,
                github_login="ada",
            )
        )
    await db.flush()
    return tenant, finding, repository


async def _proposed(
    db: AsyncSession,
    finding: FindingRow,
    repository: ConnectionRepository,
    now: datetime,
    **set_: Any,
) -> FindingFix:
    row = FindingFix(
        id=uuid.uuid4(),
        org_id=finding.org_id,
        project_id=finding.project_id,
        finding_id=finding.id,
        kind="source",
        state="proposed",
        state_changed_at=now,
        connection_repository_id=repository.id,
        folder=source.FOLDER,
        basis="likely_cause",
        files_read=_files_read(),
        checks_version=CHECKS_VERSION,
        edits=_edits(),
        evidence_ids=["file_1"],
        tokens_used=900,
        requested_by=None,
        requested_at=now,
        finished_at=now,
    )
    for name, value in set_.items():
        setattr(row, name, value)
    db.add(row)
    await db.flush()
    return row


async def _draft_check(
    db: AsyncSession,
    row: FindingFix,
    user_id: uuid.UUID,
    now: datetime,
    *,
    finished_ago: timedelta = timedelta(minutes=1),
    **set_: Any,
) -> FindingFixCheck:
    built = _rechecked()
    finished = now - finished_ago
    check = FindingFixCheck(
        id=uuid.uuid4(),
        org_id=row.org_id,
        fix_id=row.id,
        requested_by=user_id,
        purpose="draft",
        status=built.status,
        head_sha="d" * 40,
        blob_ids=built.blob_ids,
        moved_refs=list(built.moved_refs),
        patch=built.patch,
        digest=built.digest,
        requested_at=finished,
        finished_at=finished,
        expires_at=finished + timedelta(minutes=15),
    )
    for name, value in set_.items():
        setattr(check, name, value)
    db.add(check)
    await db.flush()
    return check


async def _post(db: AsyncSession, identity: Any, finding: FindingRow, tail: str, body: Any) -> Any:
    async with source.client(db, identity) as http:
        return await http.post(source.path(finding, tail), json=body)


async def _latest(db: AsyncSession, identity: Any, finding: FindingRow) -> Any:
    async with source.client(db, identity) as http:
        response = await http.get(source.path(finding, "/fixes/source"))
    assert response.status_code == 200, response.text
    return response.json()["latest"]


async def _jobs(db: AsyncSession, org_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await db.execute(
                text("SELECT task, kwargs FROM jobs WHERE org_id = :org"),
                {"org": org_id},
            )
        ).all()
    )


# ------------------------------------------------------- as shipped: off in code


def test_the_gate_ships_off() -> None:
    assert write_gate.WRITE_ACCESS_GO is None


async def test_as_shipped_every_draft_route_refuses_writes_off_and_adds_nothing(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)
    person = rows.person(tenant)

    asked = await _post(db, person, finding, f"/fixes/{row.id}/check", {"for": "draft"})
    clicked = await _post(
        db, person, finding, f"/fixes/{row.id}/draft-pull-request", {"digest": check.digest}
    )
    deleted = await _post(db, person, finding, f"/fixes/{row.id}/delete-branch", None)

    for answer in (asked, clicked, deleted):
        assert answer.status_code == 409, answer.text
        assert answer.json()["code"] == "writes_off"
        assert answer.json()["detail"] == WRITES_OFF
    assert await _jobs(db, tenant.organization.id) == []
    await db.refresh(row)
    assert (row.state, row.approved_by, row.approved_digest) == ("proposed", None, None)
    assert finding.case_state is None


async def test_as_shipped_the_read_hides_the_draft_with_the_writes_off_sentence(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await _ready(db, now)
    await _proposed(db, finding, repository, now)

    latest = await _latest(db, rows.person(tenant), finding)

    assert latest["draft"] == {
        "action": None,
        "refusal": WRITES_OFF,
        "needs_link": False,
        "link_sentence": None,
        "check": None,
        "confirm": None,
        "failure": None,
        "pull_request_state": None,
        "pull_request_url": None,
        "pull_request_sentence": None,
        "ci_note": None,
        "delete_branch_action": None,
    }
    assert latest["patch_action"] == "Prepare patch"


async def test_as_shipped_the_settings_say_writes_are_off(db: AsyncSession, now: datetime) -> None:
    tenant, _, repository = await _ready(db, now)

    async with source.client(db, rows.person(tenant)) as http:
        read = await http.get("/api/v1/connections/github/repositories/fix-settings")

    assert read.status_code == 200
    assert read.json()["writes_off"] == WRITES_OFF
    assert read.json()["repositories"][0]["drafts_allowed"] is True
    assert read.json()["repositories"][0]["repository_id"] == str(repository.id)


# --------------------------------------------- with the gate forced on, in a test


async def test_fr10c_each_refusal_hides_the_action_with_its_sentence(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now, allowed=False)
    await _proposed(db, finding, repository, now)
    person = rows.person(tenant)

    not_allowed = (await _latest(db, person, finding))["draft"]
    settings = FixRepositorySettings(
        org_id=tenant.organization.id,
        connection_repository_id=repository.id,
        drafts_allowed=True,
        set_at=now,
    )
    db.add(settings)
    app = await db.scalar(select(Connection).where(Connection.org_id == tenant.organization.id))
    assert app is not None
    app.granted_permissions = {"contents": "read", "metadata": "read"}
    await db.flush()
    no_write = (await _latest(db, person, finding))["draft"]
    app.granted_permissions = WRITE_GRANT
    settings.draft_refused_at = now - timedelta(hours=1)
    await db.flush()
    refused_by_github = (await _latest(db, person, finding))["draft"]
    settings.draft_refused_at = now - timedelta(hours=25)
    repository.is_private = False
    await db.flush()
    public = (await _latest(db, person, finding))["draft"]
    repository.public_allowed_at = now
    await db.flush()
    offered = (await _latest(db, person, finding))["draft"]

    assert (not_allowed["action"], not_allowed["refusal"]) == (
        None,
        DRAFT_REFUSAL_SENTENCES["drafts_not_allowed"],
    )
    assert no_write["refusal"] == DRAFT_REFUSAL_SENTENCES["no_write_permission"]
    assert refused_by_github["refusal"] == DRAFT_REFUSAL_SENTENCES["no_draft_support"]
    assert public["refusal"] == DRAFT_REFUSAL_SENTENCES["public_not_allowed"]
    assert (offered["action"], offered["refusal"]) == (OPEN_DRAFT, None)


async def test_d58_a_draft_needs_a_signed_deploy_as_its_likely_cause(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    explanation = await source.latest_version(db, finding)
    assert explanation is not None
    explanation.likely_cause = {"rule": "traffic_rose", "evidence_id": "deploy:d-1"}
    await db.flush()
    await _proposed(db, finding, repository, now)

    draft = (await _latest(db, rows.person(tenant), finding))["draft"]

    assert draft["refusal"] == DRAFT_REFUSAL_SENTENCES["needs_likely_cause"]


async def test_ac40_the_draft_check_needs_a_link_and_adds_one_job_with_ids_only(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now, linked=False)
    row = await _proposed(db, finding, repository, now)
    person = rows.person(tenant)

    unlinked = await _post(db, person, finding, f"/fixes/{row.id}/check", {"for": "draft"})
    db.add(
        GitHubUserLink(
            user_id=tenant.user.id,
            org_id=tenant.organization.id,
            github_user_id=9301,
            github_login="ada",
        )
    )
    await db.flush()
    asked = await _post(db, person, finding, f"/fixes/{row.id}/check", {"for": "draft"})

    assert unlinked.status_code == 409 and unlinked.json()["code"] == "not_linked"
    assert asked.status_code == 202 and asked.json()["purpose"] == "draft"
    jobs = await _jobs(db, tenant.organization.id)
    assert [job.task for job in jobs] == [CHECK_FIX_TASK]
    assert set(jobs[0].kwargs) == {"check_id", "org_id"}


async def test_fr24_a_passed_check_shows_the_confirm_by_exact_string(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)

    draft = (await _latest(db, rows.person(tenant), finding))["draft"]

    confirm = draft["confirm"]
    assert draft["action"] is None
    assert confirm["repository"] == "acme/shop"
    assert confirm["branch"] == fix_branch(row.id)
    assert confirm["facts"] == f"acme/shop, branch {fix_branch(row.id)}: 1 file, 2 lines."
    assert confirm["ci_mode"] == CONFIRM_HOLDS
    assert confirm["warnings"] == [CONFIRM_CASE, CONFIRM_AUTHOR, CONFIRM_LOGIN, CONFIRM_REACH]
    assert confirm["digest"] == check.digest
    assert confirm["public"] is False and confirm["note_public_confirm"] is None


async def test_ac61_ci_on_open_says_so_on_the_confirm(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    settings = await db.get(FixRepositorySettings, (tenant.organization.id, repository.id))
    assert settings is not None
    settings.ci_on_open = True
    row = await _proposed(db, finding, repository, now)
    await _draft_check(db, row, tenant.user.id, now)

    draft = (await _latest(db, rows.person(tenant), finding))["draft"]

    assert draft["confirm"]["ci_mode"] == CONFIRM_CI_ON_OPEN


async def test_fr22_the_click_claims_audits_starts_a_case_and_adds_the_draft_job(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)

    clicked = await _post(
        db,
        rows.person(tenant),
        finding,
        f"/fixes/{row.id}/draft-pull-request",
        {"digest": check.digest, "note": "Retries stop the checkout timeouts."},
    )

    assert clicked.status_code == 202, clicked.text
    assert clicked.json() == {"fix_id": str(row.id), "state": "approved"}
    await db.refresh(row)
    assert row.state == "approved" and row.approved_by == tenant.user.id
    assert row.approved_digest == check.digest
    assert row.requester_note == "Retries stop the checkout timeouts."
    jobs = await _jobs(db, tenant.organization.id)
    assert [job.task for job in jobs] == [OPEN_FIX_PULL_REQUEST_TASK]
    assert jobs[0].kwargs == {"fix_id": str(row.id), "org_id": str(tenant.organization.id)}
    attempted = await db.scalar(
        select(AuditLogEntry).where(
            AuditLogEntry.org_id == tenant.organization.id,
            AuditLogEntry.action == FIX_PULL_REQUEST_ATTEMPTED,
        )
    )
    assert attempted is not None and attempted.actor_user_id == tenant.user.id
    assert attempted.detail == {
        "fix_id": str(row.id),
        "repository_id": str(repository.id),
        "with_note": True,
    }
    await db.refresh(finding)
    assert finding.case_state == "investigating"
    latest = await _latest(db, rows.person(tenant), finding)
    assert (latest["heading"], latest["sentence"]) == (OPENING_HEADING, OPENING_REQUESTER)


@pytest.mark.parametrize(
    ("label", "whose", "finished_ago", "digest", "code", "sentence"),
    [
        ("another person's check", "other", timedelta(minutes=1), None, "not_your_check", None),
        ("16 minutes old", "own", timedelta(minutes=16), None, "check_expired", CHECK_EXPIRED),
        ("another digest", "own", timedelta(minutes=1), "e" * 64, "changed_since_check", None),
    ],
)
async def test_ac47_a_click_without_its_own_fresh_check_is_refused_by_a_named_code(
    db: AsyncSession,
    now: datetime,
    gate_on: None,
    label: str,
    whose: str,
    finished_ago: timedelta,
    digest: str | None,
    code: str,
    sentence: str | None,
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now)
    other = await db.scalar(
        text(
            "INSERT INTO users (org_id, email, name, role, password_hash) "
            "VALUES (:org, :email, 'Bea', 'member', 'x') RETURNING id"
        ),
        {"org": tenant.organization.id, "email": f"bea-{uuid.uuid4()}@example.test"},
    )
    owner = other if whose == "other" else tenant.user.id
    check = await _draft_check(db, row, owner, now, finished_ago=finished_ago)

    clicked = await _post(
        db,
        rows.person(tenant),
        finding,
        f"/fixes/{row.id}/draft-pull-request",
        {"digest": digest or check.digest},
    )

    assert clicked.status_code == 409, label
    assert clicked.json()["code"] == code
    expected = {
        "not_your_check": NOT_YOUR_CHECK,
        "check_expired": CHECK_EXPIRED,
        "changed_since_check": WRITE_FAILURE_SENTENCES["changed_since_check"],
    }[code]
    assert clicked.json()["detail"] == (sentence or expected)
    await db.refresh(row)
    assert row.state == "proposed" and row.approved_by is None
    assert await _jobs(db, tenant.organization.id) == []


async def test_ac25_a_second_draft_for_one_finding_is_refused_by_the_store_as_draft_open(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    held = await _proposed(
        db,
        finding,
        repository,
        now,
        state="opened",
        pull_request_branch=None,
        pull_request_state="open",
        pull_request_url="https://github.com/acme/shop/pull/7",
    )
    held.pull_request_branch = fix_branch(held.id)
    await db.flush()
    second = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, second, tenant.user.id, now)

    clicked = await _post(
        db,
        rows.person(tenant),
        finding,
        f"/fixes/{second.id}/draft-pull-request",
        {"digest": check.digest},
    )

    assert clicked.status_code == 409
    assert clicked.json()["code"] == "draft_open"
    assert "https://github.com/acme/shop/pull/7" in clicked.json()["detail"]
    await db.refresh(second)
    assert second.state == "proposed"
    savepoint = await db.begin_nested()
    with pytest.raises(IntegrityError, match="uq_finding_fixes_one_draft"):
        await db.execute(
            text(
                "UPDATE finding_fixes SET state = 'approved', approved_at = now(), "
                "approved_digest = 'x' WHERE id = :id"
            ),
            {"id": second.id},
        )
    await savepoint.rollback()


@pytest.mark.parametrize(
    ("label", "private", "body", "code"),
    [
        ("an address", True, {"note": "see https://evil.example/x"}, "note_refused"),
        ("two lines", True, {"note": "one\ntwo"}, "note_refused"),
        ("public, unconfirmed", False, {"note": "Retries help."}, "note_not_confirmed"),
    ],
)
async def test_ac62_the_requester_s_note_is_checked_and_a_public_one_needs_confirming(
    db: AsyncSession,
    now: datetime,
    gate_on: None,
    label: str,
    private: bool,
    body: dict[str, Any],
    code: str,
) -> None:
    tenant, finding, repository = await _ready(db, now)
    repository.is_private = private
    repository.public_allowed_at = None if private else now
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)

    clicked = await _post(
        db,
        rows.person(tenant),
        finding,
        f"/fixes/{row.id}/draft-pull-request",
        {"digest": check.digest, **body},
    )

    assert clicked.status_code == 422, label
    assert clicked.json()["code"] == code
    await db.refresh(row)
    assert row.state == "proposed" and row.requester_note is None


async def test_ac62_a_confirmed_public_note_is_kept(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    repository.is_private = False
    repository.public_allowed_at = now
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)

    clicked = await _post(
        db,
        rows.person(tenant),
        finding,
        f"/fixes/{row.id}/draft-pull-request",
        {"digest": check.digest, "note": "Retries help.", "note_confirmed": True},
    )

    assert clicked.status_code == 202, clicked.text
    await db.refresh(row)
    assert row.requester_note == "Retries help." and row.requester_note_confirmed is True


async def test_ac73_a_general_row_refuses_the_draft_with_a_named_code(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    acme, finding = await rows.ready(db, now)
    row = await rows.fix(db, finding, now)

    clicked = await _post(
        db, rows.person(acme), finding, f"/fixes/{row.id}/draft-pull-request", {"digest": "a" * 64}
    )

    assert clicked.status_code == 409
    assert (clicked.json()["code"], clicked.json()["detail"]) == (
        "general_has_no_code",
        GENERAL_HAS_NO_CODE,
    )


async def test_a_viewer_cannot_open_a_draft(db: AsyncSession, now: datetime, gate_on: None) -> None:
    """`MayOpenFixPullRequest`. A support view is refused below every changing route by the
    shared identity dependency, which these route tests override (status, decision 21)."""
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now)
    check = await _draft_check(db, row, tenant.user.id, now)

    viewer = await _post(
        db,
        rows.person(tenant, "viewer"),
        finding,
        f"/fixes/{row.id}/draft-pull-request",
        {"digest": check.digest},
    )

    assert viewer.status_code == 403
    await db.refresh(row)
    assert row.state == "proposed"


async def test_fr26_an_opened_draft_reads_its_state_link_and_what_holding_ci_means(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(
        db,
        finding,
        repository,
        now,
        state="opened",
        pull_request_state="open",
        pull_request_url="https://github.com/acme/shop/pull/7",
        pull_request_number=7,
    )
    row.pull_request_branch = fix_branch(row.id)
    await db.flush()

    requester = await _latest(db, rows.person(tenant), finding)
    viewer = await _latest(db, rows.person(tenant, "viewer"), finding)

    sentence = "A draft pull request is open: https://github.com/acme/shop/pull/7."
    assert (requester["heading"], requester["sentence"]) == ("Draft pull request", sentence)
    assert requester["draft"]["ci_note"] == CI_HELD_ON_PAGE
    assert requester["draft"]["pull_request_url"] == "https://github.com/acme/shop/pull/7"
    assert viewer["sentence"] == sentence and viewer["draft"]["delete_branch_action"] is None


async def test_did_not_finish_names_the_branch_left_behind(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now, last_write_failure="did_not_finish")

    draft = (await _latest(db, rows.person(tenant), finding))["draft"]

    assert draft["failure"] == (
        "Opening the draft did not finish. A branch may already exist on GitHub: "
        f"{fix_branch(row.id)}."
    )
    assert draft["action"] == OPEN_DRAFT


async def test_ac48_withdrawal_stops_a_draft_being_opened_and_takes_the_note(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(
        db,
        finding,
        repository,
        now,
        state="approved",
        approved_at=now,
        approved_by=tenant.user.id,
        approved_digest="a" * 64,
        requester_note="MARKER-note-7",
    )
    owner = rows.person(tenant, "owner")

    await fix_consent.withdraw(
        db,
        identity=owner,
        repository_id=repository.id,
        folder_scope=source.FOLDER,
        now=now,
    )

    await db.refresh(row)
    assert (row.state, row.last_write_failure) == ("proposed", "consent_withdrawn")
    assert row.requester_note is None and row.approved_digest is None and row.edits is None
    assert row.code_deleted_reason == "consent_withdrawn"


async def test_ac48_a_disconnect_marks_a_draft_it_can_no_longer_follow_untracked(
    db: AsyncSession, now: datetime, gate_on: None
) -> None:
    from nexovue_api.findings import fix_takeback

    tenant, finding, repository = await _ready(db, now)
    row = await _proposed(db, finding, repository, now, state="opened", pull_request_state="open")
    row.pull_request_branch = fix_branch(row.id)
    await db.flush()

    await fix_takeback.on_disconnect(db, tenant.organization.id, repository.connection_id)

    await db.refresh(row)
    assert row.pull_request_state == "untracked"
    holding = await db.scalar(
        select(func.count())
        .select_from(FindingFix)
        .where(
            FindingFix.finding_id == finding.id,
            FindingFix.pull_request_branch.is_not(None),
            FindingFix.pull_request_state.is_(None) | (FindingFix.pull_request_state == "open"),
        )
    )
    assert holding == 0


async def test_ac60_only_an_owner_sets_a_repository_s_draft_choices_and_each_is_audited(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, repository = await _ready(db, now, allowed=False)
    path = f"/api/v1/connections/github/repositories/{repository.id}/fix-settings"
    body = {"drafts_allowed": True, "ci_on_open": False}

    async with source.client(db, rows.person(tenant, "admin")) as http:
        admin = await http.put(path, json=body)
    async with source.client(db, rows.person(tenant, "member")) as http:
        member = await http.put(path, json=body)
    async with source.client(db, rows.person(tenant, "owner")) as http:
        owner = await http.put(path, json=body)

    assert (admin.status_code, member.status_code) == (403, 403)
    assert owner.status_code == 200, owner.text
    assert owner.json()["drafts_allowed"] is True and owner.json()["ci_on_open"] is False
    changed = await db.scalar(
        select(AuditLogEntry).where(
            AuditLogEntry.org_id == tenant.organization.id,
            AuditLogEntry.action == FIX_REPOSITORY_SETTINGS_CHANGED,
        )
    )
    assert changed is not None
    assert changed.detail == {
        "before": {"drafts_allowed": False, "ci_on_open": False},
        "after": {"drafts_allowed": True, "ci_on_open": False},
    }
