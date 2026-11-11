"""Rung 1: what F5 adds to F28's *Release before this* (spec FR1, AC34; plan T1.2).

F28's row, the first of F3's *What lines up with this*, already states the release before
a finding, how long before, and whether it came back or CI said it failed. **F5 adds no
second sentence about the same deploy.** It adds only the version that ran before it,
with a link to that release, so a person can decide on a rollback first (UR1), and
says when the release is older than F3 looks for a likely cause.

Each sentence is one fixed template, tested by exact string (`test_fix_facts.py`). None
holds a causal word: "before, not because" (`errorgroups/schemas.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.evidence import DEPLOY_LOOKBACK, display_name
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.findings.fix_schemas import DeployLineAddition, EarlierRelease, LastState
from nexovue_api.findings.schemas import ExplanationSegment
from nexovue_api.releases import queries as release_queries

# Past this, F28's own sentence reads the release as older, dimmer and unnamed
# (`finding-page.ts`, `STATED_PLAINLY_WITHIN_MS`), so F5 adds nothing to it.
STATED_PLAINLY_WITHIN: Final = timedelta(hours=24)

# The fixed templates (FR1).
RAN_BEFORE_IT: Final = " ran before it."
FAILED_SINCE: Final = " CI's last report on it said it failed."
ROLLED_BACK_SINCE: Final = " CI's last report on it said it rolled back."
NO_EARLIER_VERSION: Final = "No earlier version of this service is known."
LONGER_THAN_LOOKBACK: Final = "Longer before this than Nexovue looks for a likely cause."

_LAST_STATE_WORDS: Final = {"failed": FAILED_SINCE, "rolled_back": ROLLED_BACK_SINCE}


@dataclass(frozen=True, slots=True)
class PeriodSeen:
    """One release period as F28's reader returns it, for the addition's words."""

    release_id: uuid.UUID
    period_id: uuid.UUID
    version: str
    started_at: datetime
    already_running: bool
    last_state: LastState | None


def deploy_line_addition(
    *, started_at: datetime, before: PeriodSeen | None, earlier: PeriodSeen | None
) -> DeployLineAddition | None:
    """F5's sentences after F28's, or None when F28's own sentence is all there is: no
    release before, one already running when counting began, or one a day or more old."""
    if before is None or before.already_running:
        return None
    elapsed = started_at - before.started_at
    if elapsed >= STATED_PLAINLY_WITHIN:
        return None
    sentences = [_earlier_sentence(earlier)]
    if elapsed > DEPLOY_LOOKBACK:
        sentences.append([ExplanationSegment(text=LONGER_THAN_LOOKBACK)])
    return DeployLineAddition(
        release_id=before.release_id,
        earlier=None
        if earlier is None
        else EarlierRelease(
            release_id=earlier.release_id,
            version=earlier.version,
            last_state=earlier.last_state,
        ),
        sentences=sentences,
    )


def _earlier_sentence(earlier: PeriodSeen | None) -> list[ExplanationSegment]:
    if earlier is None:
        return [ExplanationSegment(text=NO_EARLIER_VERSION)]
    said = RAN_BEFORE_IT + _LAST_STATE_WORDS.get(earlier.last_state or "", "")
    return [ExplanationSegment(name=display_name(earlier.version)), ExplanationSegment(text=said)]


async def read_deploy_line(session: AsyncSession, row: FindingRow) -> DeployLineAddition | None:
    """F28's release before the finding, by F28's own reader, and the one before that.
    A project finding names no one service's deploy, so F5 adds nothing (FR1)."""
    if row.service_name is None:
        return None
    keys: dict[str, Any] = {
        "org_id": row.org_id,
        "project_id": row.project_id,
        "service_name": row.service_name,
    }
    before = await release_queries.period_before(session, **keys, at=row.started_at)
    earlier = (
        None
        if before is None
        else await release_queries.period_preceding(
            session,
            **keys,
            release_id=before.release_id,
            started_at=before.started_at,
            period_id=before.period_id,
        )
    )
    return deploy_line_addition(
        started_at=row.started_at, before=_seen(before), earlier=_seen(earlier)
    )


def _seen(found: Any | None) -> PeriodSeen | None:
    if found is None:
        return None
    return PeriodSeen(
        release_id=found.release_id,
        period_id=found.period_id,
        version=found.version,
        started_at=found.started_at,
        already_running=found.already_running,
        last_state=found.last_state,
    )
