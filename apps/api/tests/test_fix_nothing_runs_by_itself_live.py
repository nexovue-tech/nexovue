"""Nothing asks for a general suggestion but a person's click (F5 plan T2b.11, first version;
spec AC1, AC2 and AC73's last half for `insight.suggest_general_fix`).

**AC2, read from source:** the only code that builds the job is the click's
(`findings/fix_general.py`); beat schedules it nowhere, and no other module names the task.
**AC1, against real Postgres:** with a finding fully eligible, reading the page's two reads
over and over writes no suggestion and adds no job: only `POST` does. Step 4 extends both to
rung 3's tasks (T4.9).

The live half's rows are inside a rolled-back transaction; it fails rather than skips when
Postgres is down.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Final

import general_fix_rows as rows
from general_fix_rows import POSTGRES_DSN
from live import require_stack
from nexovue_platform.fixes.records import SUGGEST_GENERAL_FIX_TASK
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import FindingFix, Job
from nexovue_api.settings import API_PREFIX

REPOSITORY: Final = Path(__file__).resolve().parents[3]
SOURCES: Final = (
    REPOSITORY / "apps/api/src",
    REPOSITORY / "apps/scheduler/src",
    REPOSITORY / "packages/py-platform/src",
)
BUILDER: Final = "suggest_general_fix_request"
TASK_NAME: Final = "SUGGEST_GENERAL_FIX_TASK"
# Where the job may be built (the click) and named (its definition and its task).
BUILT_IN: Final = {"apps/api/src/nexovue_api/findings/fix_general.py"}
NAMED_IN: Final = {
    "packages/py-platform/src/nexovue_platform/fixes/records.py",
    "apps/scheduler/src/nexovue_scheduler/insight/suggest_general_fix.py",
}
READS: Final = 5

db = rows.db
now = rows.now

require_stack(
    reachable=rows.reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_nothing_runs_by_itself_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


def _modules_using(name: str) -> set[str]:
    found = set()
    for root in SOURCES:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            used = any(
                (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.Attribute) and node.attr == name)
                for node in ast.walk(tree)
            )
            if used:
                found.add(path.relative_to(REPOSITORY).as_posix())
    return found


def test_ac2_only_the_click_builds_the_general_suggestion_s_job() -> None:
    builders = _modules_using(BUILDER) - {
        "packages/py-platform/src/nexovue_platform/fixes/records.py"
    }

    assert builders == BUILT_IN


def test_ac2_no_other_module_names_the_task_beat_included() -> None:
    assert _modules_using(TASK_NAME) == NAMED_IN


async def test_ac1_reading_the_page_never_asks_for_a_suggestion(
    db: AsyncSession, now: datetime
) -> None:
    acme, finding = await rows.ready(db, now)

    async with rows.client(db, rows.person(acme)) as http:
        for _ in range(READS):
            assert (await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes")).status_code == 200
            general = await http.get(f"{API_PREFIX}/findings/{finding.id}/fixes/general")
            assert general.json()["offer"] is not None

    fixes = await db.scalar(
        select(func.count()).select_from(FindingFix).where(FindingFix.finding_id == finding.id)
    )
    jobs = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.org_id == acme.organization.id, Job.task == SUGGEST_GENERAL_FIX_TASK)
    )
    assert (fixes, jobs) == (0, 0)
