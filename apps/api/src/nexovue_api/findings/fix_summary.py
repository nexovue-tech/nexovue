"""The admin summary of suggested fixes over the last 30 days (F5 spec UR12, AC39; design, *The
summary*; plan T9.3).

Counts only, read from columns that already exist, with no claim of value: how many were
asked for, refused and why, what people said of them, drafts opened and merged, and tokens.
Every count is a row count or a sum of a column that is never null, so a 0 is a true 0. A
notice is each `fix.withheld` entry the sweep wrote once for a rule that may mean planted text
(`nexovue_platform.fixes.notices`): the rule, when, and the files it named, never the
suggestion's text. A support view reads no paths (AC11).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.notices import notice_rules, notice_sentence
from nexovue_platform.fixes.records import FIX_VERDICTS, FIX_WITHHELD, REFUSED
from nexovue_platform.fixes.sentences import (
    REASON_SENTENCES,
    VERDICT_LABELS,
    WITHHELD_RULE_WORDS,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import AuditLogEntry, FindingFix
from nexovue_api.findings.fix_schemas import (
    FixSummary,
    FixSummaryNotice,
    FixSummaryRefusal,
    FixSummaryVerdict,
)

SUMMARY_DAYS: Final = 30
SUMMARY_EMPTY: Final = "No suggested fixes asked for in the last 30 days."
WITHHELD_PREFIX: Final = "withheld_"
# A screenful: the newest notices first; the audit log holds every one.
MAX_NOTICES: Final = 50


async def summary(
    db: AsyncSession, *, org_id: uuid.UUID, now: datetime, support_view: bool = False
) -> FixSummary:
    since = now - timedelta(days=SUMMARY_DAYS)
    requested, tokens = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(FindingFix.tokens_used), 0)).where(
                FindingFix.org_id == org_id,
                FindingFix.requested_at > since,
                FindingFix.requested_at <= now,
            )
        )
    ).one()
    refused = (
        await db.execute(
            select(FindingFix.reason, func.count())
            .where(
                FindingFix.org_id == org_id,
                FindingFix.state == REFUSED,
                FindingFix.requested_at > since,
                FindingFix.requested_at <= now,
            )
            .group_by(FindingFix.reason)
            .order_by(func.count().desc(), FindingFix.reason)
        )
    ).all()
    verdicts = dict(
        (
            await db.execute(
                select(FindingFix.verdict, func.count())
                .where(
                    FindingFix.org_id == org_id,
                    FindingFix.verdict_at > since,
                    FindingFix.verdict_at <= now,
                )
                .group_by(FindingFix.verdict)
            )
        )
        .tuples()
        .all()
    )
    opened = await db.scalar(
        select(func.count()).where(
            FindingFix.org_id == org_id,
            FindingFix.pull_request_number.is_not(None),
            FindingFix.approved_at > since,
            FindingFix.approved_at <= now,
        )
    )
    merged = await db.scalar(
        select(func.count()).where(
            FindingFix.org_id == org_id,
            FindingFix.pull_request_state == "merged",
            FindingFix.pull_request_state_at > since,
            FindingFix.pull_request_state_at <= now,
        )
    )
    return FixSummary(
        days=SUMMARY_DAYS,
        empty=SUMMARY_EMPTY,
        requested=int(requested),
        refused=[
            FixSummaryRefusal(reason=reason, label=_refusal_label(reason), count=int(count))
            for reason, count in refused
            if reason is not None
        ],
        verdicts=[
            FixSummaryVerdict(
                verdict=verdict, label=VERDICT_LABELS[verdict], count=int(verdicts.get(verdict, 0))
            )
            for verdict in FIX_VERDICTS
        ],
        drafts_opened=int(opened or 0),
        drafts_merged=int(merged or 0),
        tokens=int(tokens),
        notices=await _notices(db, org_id=org_id, since=since, now=now, support_view=support_view),
    )


def _refusal_label(reason: str) -> str:
    rule = reason.removeprefix(WITHHELD_PREFIX)
    if reason.startswith(WITHHELD_PREFIX) and rule in WITHHELD_RULE_WORDS:
        return f"Rule {rule}: {WITHHELD_RULE_WORDS[rule]}"
    return REASON_SENTENCES[reason].requester


async def _notices(
    db: AsyncSession, *, org_id: uuid.UUID, since: datetime, now: datetime, support_view: bool
) -> list[FixSummaryNotice]:
    shown = notice_rules()
    entries = (
        await db.execute(
            select(AuditLogEntry.at, AuditLogEntry.target, AuditLogEntry.detail)
            .where(
                AuditLogEntry.org_id == org_id,
                AuditLogEntry.action == FIX_WITHHELD,
                AuditLogEntry.at > since,
                AuditLogEntry.at <= now,
                AuditLogEntry.detail["rule"].astext.in_(shown),
            )
            .order_by(AuditLogEntry.at.desc(), AuditLogEntry.id.desc())
            .limit(MAX_NOTICES)
        )
    ).all()
    return [
        _notice(at, target, detail or {}, support_view=support_view)
        for at, target, detail in entries
    ]


def _notice(
    at: datetime, target: str, detail: dict[str, Any], *, support_view: bool
) -> FixSummaryNotice:
    rule = str(detail["rule"])
    prefix = f"{FINDING_AUDIT_TARGET}:"
    files = [str(path) for path in detail.get("files") or [] if isinstance(path, str)]
    return FixSummaryNotice(
        rule=rule,
        sentence=notice_sentence(rule),
        at=at,
        fix_id=_uuid(detail.get("fix_id")),
        finding_id=_uuid(target.removeprefix(prefix)) if target.startswith(prefix) else None,
        files=None if support_view else files,
    )


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
