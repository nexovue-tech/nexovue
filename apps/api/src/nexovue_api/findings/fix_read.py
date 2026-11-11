"""`GET /findings/{id}/fixes`: rungs 1 and 2 as one person reads them (spec FR1 to FR5;
design, *Routes*; plan T1.3).

Postgres only and no GitHub call, so the page can poll it. **It works with AI off,** since
it reads no provider, no explanation words and no `wording_state` (design, *Interfaces F5
takes and gives*): only the finding, F28's releases and F3's stored facts.

**A held cause stays held** (F3 FR50): the brief names a likely cause only once its rule
is released, exactly as the explanation's own read does, so copying a brief can never
reveal a cause the page does not show.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from nexovue_platform.deployment import HOSTED
from nexovue_platform.evidence import cause_released
from nexovue_platform.fixes.brief import (
    BriefCause,
    BriefInputs,
    BriefSignal,
    ConsoleLink,
    build_brief,
    error_groups_from,
    last_deploy_from,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingExplanation
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.findings import explanation_queries, fix_facts
from nexovue_api.findings.fix_schemas import FindingFixes, FixBrief
from nexovue_api.findings.queries import find_finding
from nexovue_api.findings.service import parse_signals
from nexovue_api.settings import Settings

NOT_FOUND: Final = "No such finding."
# FR5: closed as not a problem, so there is nothing to brief anyone about.
NO_BRIEF_WHEN_CLOSED_AS: Final = frozenset({"false_alarm", "service_gone"})


async def read_fixes(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> FindingFixes:
    """Rung 1's additions and the brief. Another organization's finding is a 404 like a
    missing one. The section is absent: no role has a line before plan Step 2b."""
    row = await find_finding(session, org_id=org_id, finding_id=finding_id)
    if row is None:
        raise NotFoundError(NOT_FOUND)
    explanation = await explanation_queries.version(
        session, org_id=org_id, finding_id=finding_id, number=None
    )
    return FindingFixes(
        finding_id=row.id,
        deploy_line=await fix_facts.read_deploy_line(session, row),
        brief=None
        if row.closed_reason in NO_BRIEF_WHEN_CLOSED_AS
        else FixBrief(text=build_brief(_brief_inputs(row, explanation, settings, now))),
        section=None,
    )


def _brief_inputs(
    row: FindingRow, explanation: FindingExplanation | None, settings: Settings, now: datetime
) -> BriefInputs:
    evidence: dict[str, Any] = {} if explanation is None else explanation.evidence
    return BriefInputs(
        scope="project" if row.service_name is None else "service",
        started_at=row.started_at,
        signals=[
            BriefSignal(
                signal=signal.signal,
                unit=signal.unit,
                normal=signal.normal,
                current=signal.current,
                worst=signal.worst,
                endpoint=signal.endpoint,
            )
            for signal in parse_signals(row.signals)
        ],
        explanation_read=explanation is not None,
        cause=_released_cause(explanation, settings),
        last_deploy=last_deploy_from(evidence, started_at=row.started_at),
        error_groups=error_groups_from(evidence),
        # Code locations come with plan Step 3 (`service_code_locations`).
        code_location=None,
        repository_line=settings.nexovue_deployment != HOSTED,
        console=None
        if not settings.app_base_url
        else ConsoleLink(
            base_url=settings.app_base_url,
            project_id=row.project_id,
            finding_id=row.id,
            start=row.started_at,
            end=row.closed_at or now,
        ),
    )


def _released_cause(
    explanation: FindingExplanation | None, settings: Settings
) -> BriefCause | None:
    stored = None if explanation is None else explanation.likely_cause
    if stored is None or not cause_released(stored.get("rule"), override=settings.show_held_causes):
        return None
    rule, strength = stored.get("rule"), stored.get("strength")
    if not isinstance(rule, str) or not isinstance(strength, str):
        return None
    return BriefCause(rule=rule, strength=strength)
