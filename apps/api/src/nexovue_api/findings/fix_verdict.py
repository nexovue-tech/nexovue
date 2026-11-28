""" "Was this suggestion useful?" (spec FR29, UR11; design, `finding_fixes`, *State by role*;
plan T5.2; status, open decision 25).

One answer per suggestion, the last person's: `verdict`, `verdict_by`, `verdict_at`, and
**nothing else changes**: only those attributes are set, so the one UPDATE names only them,
and no state, code, tries or timestamp moves (a test compares the whole row). Asked of a
written suggestion of either kind, and of a rung 3 suggestion gone out of date, since a
person may have used it before it did; its code being deleted does not stop the answer,
which holds no code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.records import (
    FIX_VERDICTS,
    KIND_SOURCE,
    PROPOSED,
    STALE,
    FixVerdict,
)
from nexovue_platform.fixes.sentences import (
    NO_WRITTEN_SUGGESTION,
    VERDICT_LABELS,
    VERDICT_QUESTION,
    VERDICT_RECORDED,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.dependencies import Identity
from nexovue_api.data.postgres.models import FindingFix
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.findings import fix_queries
from nexovue_api.findings.fix_schemas import VerdictAnswer, VerdictView

NO_SUCH_FIX: Final = "No such suggestion for this finding."
NOT_ANSWERABLE_CODE: Final = "no_written_suggestion"


def answerable(row: FindingFix) -> bool:
    """A written suggestion; a rung 3 one also once it is out of date."""
    return row.state == PROPOSED or (row.kind == KIND_SOURCE and row.state == STALE)


def view(chosen: str | None) -> VerdictView:
    known: FixVerdict | None = next((each for each in FIX_VERDICTS if each == chosen), None)
    return VerdictView(
        question=VERDICT_QUESTION,
        answers=[VerdictAnswer(value=each, label=VERDICT_LABELS[each]) for each in FIX_VERDICTS],
        chosen=known,
        recorded=None if known is None else VERDICT_RECORDED,
    )


async def record(
    db: AsyncSession,
    *,
    identity: Identity,
    finding_id: uuid.UUID,
    fix_id: uuid.UUID,
    verdict: FixVerdict,
    now: datetime,
) -> VerdictView:
    org_id = identity.org_id
    row = await fix_queries.fix_of(db, org_id=org_id, finding_id=finding_id, fix_id=fix_id)
    if row is None:
        raise NotFoundError(NO_SUCH_FIX)
    if not answerable(row):
        raise ConflictError(NO_WRITTEN_SUGGESTION, code=NOT_ANSWERABLE_CODE)
    # Only these three attributes change, so the flush's UPDATE names only them.
    row.verdict, row.verdict_by, row.verdict_at = verdict, identity.user.id, now
    await db.commit()
    return view(verdict)
