"""How a finding's explanations do across the organization, and stopping a rule from
naming causes (F3 FR54, A64; `/settings/explanations`).

Postgres only, so this is on the default rate-limit tier. Counts only ever leave here:
no name, no finding, no person, so the summary is safe to copy and send (AC99). The
service is the explanation's own (`findings/explanations.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from nexovue_api.auth.policy import MayConfigureDetection
from nexovue_api.deps import AppSettings, DbSession
from nexovue_api.findings import explanations
from nexovue_api.findings.schemas import RuleCountsResponse, RuleName, StoppedRule

router = APIRouter(prefix="/explanations", tags=["findings"])


@router.get(
    "/rule-counts", summary="Each rule's findings and ratings, as counts; and which are stopped"
)
async def read_rule_counts(
    identity: MayConfigureDetection, db: DbSession, settings: AppSettings
) -> RuleCountsResponse:
    """Counts only, never a name, so the summary is safe to copy and send (AC99)."""
    return await explanations.rule_counts(
        db, org_id=identity.org_id, show_held_causes=settings.show_held_causes
    )


@router.put("/stopped-rules/{rule}", summary="Stop a rule naming causes in this organization")
async def stop_rule(identity: MayConfigureDetection, db: DbSession, rule: RuleName) -> StoppedRule:
    """Its facts stay on every explanation; it names no cause here. Audited."""
    return await explanations.set_rule_stopped(
        db,
        org_id=identity.org_id,
        person_id=identity.user.id,
        rule=rule,
        stopped=True,
        now=datetime.now(UTC),
    )


@router.delete("/stopped-rules/{rule}", summary="Let a stopped rule name causes again")
async def resume_rule(
    identity: MayConfigureDetection, db: DbSession, rule: RuleName
) -> StoppedRule:
    return await explanations.set_rule_stopped(
        db,
        org_id=identity.org_id,
        person_id=identity.user.id,
        rule=rule,
        stopped=False,
        now=datetime.now(UTC),
    )
