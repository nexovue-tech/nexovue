"""The readiness counts (spec FR10b, AC44; design, *The readiness panel*; plan T3.6).

Before any permission is raised, an owner sees how many of a project's last 30 days of
findings would reach each result, from F3's stored evidence alone: no GitHub call and no
ClickHouse read. "Counts only the evidence Nexovue holds; whether files match is known only
after a read." A held cause reads as no cause, exactly as the page shows it (F3 FR50).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.evidence import RULE_DEPENDENCY_FINDING, cause_released
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.connections.fix_schemas import ReadinessCounts
from nexovue_api.data.postgres.models import Finding, FindingExplanation, Project
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.findings import fix_access
from nexovue_api.findings.fix_eligibility import NOT_A_PROBLEM

READINESS_WINDOW: Final = timedelta(days=30)
COUNTS_SENTENCE: Final = (
    "Counts only the evidence Nexovue holds; whether files match is known only after a read."
)
EMPTY_SENTENCE: Final = "No findings in the last 30 days, so there is nothing to estimate yet."
_NO_PROJECT: Final = "No such project."


async def readiness(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, show_held: bool, now: datetime
) -> ReadinessCounts:
    project = await db.scalar(
        select(Project.id).where(Project.org_id == org_id, Project.id == project_id)
    )
    if project is None:
        raise NotFoundError(_NO_PROJECT)
    latest = (
        select(
            FindingExplanation.finding_id,
            func.max(FindingExplanation.version).label("version"),
        )
        .where(FindingExplanation.org_id == org_id, FindingExplanation.project_id == project_id)
        .group_by(FindingExplanation.finding_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Finding, FindingExplanation)
            .outerjoin(latest, latest.c.finding_id == Finding.id)
            .outerjoin(
                FindingExplanation,
                (FindingExplanation.org_id == Finding.org_id)
                & (FindingExplanation.finding_id == Finding.id)
                & (FindingExplanation.version == latest.c.version),
            )
            .where(
                Finding.org_id == org_id,
                Finding.project_id == project_id,
                Finding.started_at >= now - READINESS_WINDOW,
            )
        )
    ).all()
    counts = {"ready": 0, "ready_without_stack": 0, "cause_is_elsewhere": 0}
    project_findings = closed = 0
    services: set[str] = set()
    for finding, explanation in rows:
        if finding.service_name is None:
            project_findings += 1
            continue
        if finding.closed_reason in NOT_A_PROBLEM:
            closed += 1
            continue
        services.add(finding.service_name)
        counts[_result(explanation, show_held=show_held)] += 1
    without = [
        name
        for name in sorted(services)
        if await fix_access.location_of(db, org_id=org_id, project_id=project_id, service_name=name)
        is None
    ]
    return ReadinessCounts(
        findings=len(rows),
        ready=counts["ready"],
        ready_without_stack=counts["ready_without_stack"],
        cause_is_elsewhere=counts["cause_is_elsewhere"],
        project_finding=project_findings,
        closed_as_not_a_problem=closed,
        services_without_location=without,
        sentence=COUNTS_SENTENCE if rows else EMPTY_SENTENCE,
    )


def _result(explanation: FindingExplanation | None, *, show_held: bool) -> str:
    """The design's *What reaches which result*, keyed on the kind of cause F3 stored."""
    cause: Mapping[str, Any] | None = None if explanation is None else explanation.likely_cause
    rule = None if cause is None else cause.get("rule")
    released = isinstance(rule, str) and cause_released(rule, override=show_held)
    if released and rule == RULE_DEPENDENCY_FINDING:
        return "cause_is_elsewhere"
    items = [] if explanation is None else explanation.evidence.get("items") or []
    linked = any(isinstance(item, Mapping) and item.get("kind") == "error_group" for item in items)
    return "ready" if linked else "ready_without_stack"
