"""The facts a draft's refusals are decided from, read for a `draft` check run and again by
the draft job before it writes (spec FR22, BR13, BR14, D58; plan T7.4, T7.5). The rule itself
is `nexovue_platform/fixes/draft.py`; this reads what it needs.

Every statement binds the job's own `org_id` (`test_scheduler_postgres_tenancy_structure.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from nexovue_platform.evidence import cause_released
from nexovue_platform.fixes.draft import DRAFT_CAUSE_RULES, draft_refusal, public_refused
from nexovue_platform.fixes.records import PUBLIC_NOT_ALLOWED
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections.fix_github import RepositoryFacts

_FACTS: Final = text(
    """
    SELECT settings.drafts_allowed, settings.ci_on_open, settings.draft_refused_at,
           connection.granted_permissions, place.public_allowed_at
    FROM connection_repositories AS place
    JOIN connections AS connection
      ON connection.org_id = place.org_id AND connection.id = place.connection_id
    LEFT JOIN fix_repository_settings AS settings
      ON settings.org_id = place.org_id AND settings.connection_repository_id = place.id
    WHERE place.org_id = :org_id AND place.id = :repository_id
    """
)
_CAUSE: Final = text(
    """
    SELECT likely_cause FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id AND version = :version
    """
)


async def repository_facts(engine: AsyncEngine, suggestion: Any) -> Any | None:
    """The owner's draft choices, the App's grant and the public allowance, or None."""
    if suggestion.connection_repository_id is None:
        return None
    async with engine.connect() as connection:
        return (
            await connection.execute(
                _FACTS,
                {
                    "org_id": suggestion.org_id,
                    "repository_id": suggestion.connection_repository_id,
                },
            )
        ).one_or_none()


async def _signed_deploy_cause(
    engine: AsyncEngine, suggestion: Any, *, show_held_causes: bool
) -> bool:
    """D58: the suggestion's likely cause is this service's own signed deploy, released."""
    if suggestion.explanation_version is None:
        return False
    async with engine.connect() as connection:
        cause = await connection.scalar(
            _CAUSE,
            {
                "org_id": suggestion.org_id,
                "finding_id": suggestion.finding_id,
                "version": suggestion.explanation_version,
            },
        )
    rule = cause.get("rule") if isinstance(cause, dict) else None
    return rule in DRAFT_CAUSE_RULES and cause_released(rule, override=show_held_causes)


async def refusal_before_github(
    engine: AsyncEngine, suggestion: Any, *, show_held_causes: bool, now: datetime
) -> str | None:
    """The four refusals that need no GitHub call, in `draft.py`'s order."""
    facts = await repository_facts(engine, suggestion)
    return draft_refusal(
        drafts_allowed=facts is not None and bool(facts.drafts_allowed),
        granted=None if facts is None else facts.granted_permissions,
        draft_refused_at=None if facts is None else facts.draft_refused_at,
        signed_deploy_cause=await _signed_deploy_cause(
            engine, suggestion, show_held_causes=show_held_causes
        ),
        now=now,
    )


async def public_refusal(engine: AsyncEngine, row: Any, live: RepositoryFacts) -> str | None:
    """BR13 on GitHub's live answer: a public repository needs an admin's allowance."""
    async with engine.connect() as connection:
        allowed = await connection.scalar(
            text(
                "SELECT public_allowed_at FROM connection_repositories "
                "WHERE org_id = :org_id AND id = :repository_id"
            ),
            {"org_id": row.org_id, "repository_id": row.connection_repository_id},
        )
    if public_refused(private=live.private, public_allowed=allowed is not None):
        return PUBLIC_NOT_ALLOWED
    return None
