"""Everything a suggested-fix read or click decides from, read once (spec FR9, FR31; plan
T2b.7, T3.7). Shared by rung 2b's routes and rung 3's, so both stand on one FR9 list.

**Reads AI code** (the AI connection in use, F3's limits): rung 1 and 2's read never
imports this module, so the deploy line and the brief keep working with AI off (AC33).
Postgres only: no GitHub call and no ClickHouse read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nexovue_platform.ai.limits import fix_ceiling, month_of
from nexovue_platform.deployment import HOSTED
from nexovue_platform.evidence import cause_released, facts_may_be_sent
from nexovue_platform.fixes.records import TRIES_WINDOW
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai import queries as ai_queries
from nexovue_api.ai import service as ai_service
from nexovue_api.data.postgres.models import AIConnection, FindingExplanation
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.findings import explanation_queries, fix_access, fix_queries
from nexovue_api.findings.fix_access import CodeAccess
from nexovue_api.findings.fix_eligibility import (
    Eligibility,
    EligibilityFacts,
    SourceEligibility,
    general_eligibility,
    source_eligibility,
)
from nexovue_api.settings import Settings


@dataclass(frozen=True, slots=True)
class FixState:
    finding: FindingRow
    explanation: FindingExplanation | None
    connection: AIConnection | None
    access: CodeAccess
    facts: EligibilityFacts
    eligibility: Eligibility
    source: SourceEligibility
    tries_used: list[datetime]
    tokens_out: bool
    released_rule: str | None
    now: datetime


async def gather(
    session: AsyncSession, finding: FindingRow, *, settings: Settings, now: datetime
) -> FixState:
    org_id = finding.org_id
    explanation = await explanation_queries.version(
        session, org_id=org_id, finding_id=finding.id, number=None
    )
    hosted = settings.nexovue_deployment == HOSTED
    connection = (
        None
        if hosted
        else await ai_service.connection_in_use(
            session, org_id=org_id, availability=ai_service.availability_of(settings)
        )
    )
    access = await fix_access.code_access(
        session,
        org_id=org_id,
        project_id=finding.project_id,
        service_name=finding.service_name,
        current=fix_access.terms_of(connection),
    )
    opted_out = await ai_queries.opted_out(session, org_id=org_id, project_id=finding.project_id)
    tries = await fix_queries.counted_since(
        session, org_id=org_id, finding_id=finding.id, since=now - TRIES_WINDOW
    )
    tokens_out = await _tokens_out(session, connection, now=now)
    likely_cause = None if explanation is None else explanation.likely_cause
    released = _released_rule(likely_cause, override=settings.show_held_causes)
    model_input = None if explanation is None else explanation.model_input
    facts = EligibilityFacts(
        hosted=hosted,
        ai_on=connection is not None,
        opted_out=opted_out,
        project_scope=finding.service_name is None,
        closed_reason=finding.closed_reason,
        opened_at=finding.started_at,
        error_group_linked=has_error_group(explanation),
        released_cause_rule=released,
        sendable_facts=model_input is not None
        and facts_may_be_sent(likely_cause, override=settings.show_held_causes),
        cause_or_error_alias=released is not None or _names_an_error(model_input),
        tokens_left=not tokens_out,
        tries_used=len(tries),
        in_flight=await fix_queries.in_flight(session, org_id=org_id, finding_id=finding.id),
        code_location=access.location is not None,
        read_permission=access.read_permission,
        consent_live=access.consent is not None,
    )
    return FixState(
        finding=finding,
        explanation=explanation,
        connection=connection,
        access=access,
        facts=facts,
        eligibility=general_eligibility(facts, now=now),
        source=source_eligibility(facts, now=now),
        tries_used=tries,
        tokens_out=tokens_out,
        released_rule=released,
        now=now,
    )


async def _tokens_out(
    session: AsyncSession, connection: AIConnection | None, *, now: datetime
) -> bool:
    """D32: the organization's used and reserved tokens have reached F5's share."""
    if connection is None or connection.monthly_token_limit is None:
        return False
    usage = await ai_queries.usage_month(session, org_id=connection.org_id, month=month_of(now))
    spent = 0 if usage is None else usage.used_tokens + usage.reserved_tokens
    return spent >= fix_ceiling(connection.monthly_token_limit)


def _released_rule(likely_cause: Mapping[str, Any] | None, *, override: bool) -> str | None:
    rule = None if likely_cause is None else likely_cause.get("rule")
    return rule if isinstance(rule, str) and cause_released(rule, override=override) else None


def has_error_group(explanation: FindingExplanation | None) -> bool:
    items = [] if explanation is None else explanation.evidence.get("items") or []
    return any(isinstance(item, Mapping) and item.get("kind") == "error_group" for item in items)


def _names_an_error(model_input: Mapping[str, Any] | None) -> bool:
    """D66: an `error_group` fact carrying an `error_N` alias, which exists only when F3's
    identifier pattern passed."""
    facts = [] if model_input is None else model_input.get("facts") or []
    return any(
        isinstance(fact, Mapping)
        and fact.get("kind") == "error_group"
        and isinstance(fact.get("error"), str)
        for fact in facts
    )
