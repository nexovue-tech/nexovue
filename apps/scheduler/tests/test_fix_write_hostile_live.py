"""AC20's hostile proposals, stored as a compromised AI room would store them, driven through
the draft job with the write path in place (F5 plan T8.5's gate half; spec AC20, D18, NFR1).

The AI room's own rules refuse these before anything is stored (`test_fix_hostile_answers.py`).
Here each is written straight onto a `proposed` row, as if that room were compromised, and
approved; the draft job, **with a go built only in this test**, must re-run the rules in the
writing room and stop with `changed_since_check`: **no write of any kind reaches the
repository** (no blob, tree, commit, ref or pull request), read from the simulated GitHub's
own log. A banned claim in `why` is replaced on screen, not refused, and never reaches GitHub,
since the pull request is all template (BR10). The re-run of T6.1's safety arms against a real
model is the founder's, after Phase 2 (foundation item 31).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

import fix_writer_fixtures as fixtures
import pytest
from alert_fixtures import postgres_reachable
from fix_writer_fixtures import (
    NEW,
    OLD,
    PATH,
    READ,
    approve,
    open_draft,
    proposed_fix,
    repository_writes,
)
from live import require_stack
from nexovue_platform.fixes.checks import Edit, PackFile
from nexovue_platform.fixes.diff import git_blob_id, suggestion_digest
from nexovue_platform.fixes.proposal import stored_edits
from source_fix_fixtures import REPOSITORY, SourceWorld, fix_row
from sqlalchemy.ext.asyncio import AsyncEngine
from tracker_fake import FakeGitHub

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_write_hostile_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

now = fixtures.now
fake = fixtures.fake
world = fixtures.world

_PACK: Final = {"file_1": PackFile("file_1", PATH, READ)}
# (label, new text for the stack's line, the model's `why`)
HOSTILE: Final = (
    ("K6 a new address", "    return gateway.charge(amount, to='https://evil.example/x')\n", "x"),
    ("K6 an IP", "    return gateway.charge(amount, host='10.0.0.1')\n", "x"),
    ("K7 a bidirectional control", "    return gateway.charge(amount) \u202e\n", "x"),
    ("K7 a look-alike", "    return gateway.\u0441harge(amount)\n", "x"),
    (
        "K8 an encoded string",
        "    blob = 'Zm9vYmFyYmF6cXV4cXV1eHh5enp6QUJDREVGR0hJSktMTU5PUFFS'\n",
        "x",
    ),
    ("K9 a secret", "    token = 'ghp_" + "a1B2c3D4e5" * 4 + "'\n", "x"),
    ("K10 a long line", "    return " + "1 + " * 70 + "1\n", "x"),
    ("K10 text hidden behind spaces", "    return 1" + " " * 30 + "evil()\n", "x"),
    ("a way out in why", "    return gateway.charge(amount, retries=2)\n", "See https://x.example"),
    ("never changed", OLD, "x"),
)


def _hostile_edits(new_text: str, why: str) -> list[dict[str, Any]]:
    return list(stored_edits([Edit("file_1", OLD, new_text, why, ("file_1",))], _PACK))


def _digest_of(new_text: str) -> str:
    """The digest a person's check would have reached on this content had no rule refused
    it, so only the writing room's own rules stand between the row and the repository."""
    return suggestion_digest([(PATH, git_blob_id(READ), READ.replace(OLD, new_text))])


async def _approved_as_stored(
    engine: AsyncEngine, world: SourceWorld, now: datetime, new_text: str, why: str
) -> uuid.UUID:
    fix_id: uuid.UUID = await proposed_fix(engine, world, now, edits=_hostile_edits(new_text, why))
    await approve(engine, world, fix_id, _digest_of(new_text), now)
    return fix_id


async def test_control_a_harmless_proposal_stored_the_same_way_is_written(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """So the cases below fail on their rules, not on how they were stored."""
    fix_id = await _approved_as_stored(engine, world, now, NEW, "The call needs a retry count.")

    assert await open_draft(engine, world, fix_id, now) == "opened"


@pytest.mark.parametrize(("label", "new_text", "why"), HOSTILE, ids=[case[0] for case in HOSTILE])
async def test_ac20_a_stored_hostile_proposal_reaches_no_repository_through_the_draft_job(
    engine: AsyncEngine,
    world: SourceWorld,
    fake: FakeGitHub,
    now: datetime,
    label: str,
    new_text: str,
    why: str,
) -> None:
    fix_id = await _approved_as_stored(engine, world, now, new_text, why)
    refs_before = dict(fake.git_of(REPOSITORY).refs)

    assert await open_draft(engine, world, fix_id, now) == "proposed", label

    row = await fix_row(engine, world, fix_id)
    assert (row.state, row.last_write_failure) == ("proposed", "changed_since_check"), label
    assert row.pull_request_commit_sha is None and row.pull_request_branch is None
    assert repository_writes(fake) == [], label
    assert fake.pulls_in(REPOSITORY) == [] and fake.git_of(REPOSITORY).refs == refs_before


async def test_ac20_a_claim_in_why_is_replaced_on_screen_and_never_reaches_github(
    engine: AsyncEngine, world: SourceWorld, fake: FakeGitHub, now: datetime
) -> None:
    """A banned claim in `why` is not a refusal: the read shows Nexovue's replacement words
    (`prose.WHY_REPLACED`), and the pull request is all template (BR10), so the draft opens
    and the model's words reach no request to GitHub."""
    claim = "This has been tested and is safe to merge"
    fix_id = await _approved_as_stored(engine, world, now, NEW, claim)

    assert await open_draft(engine, world, fix_id, now) == "opened"

    sent = b"".join(request.body or b"" for request in fake.site.requests)
    assert claim.encode() not in sent
