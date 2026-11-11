"""Chat's consent preview, `GET /ai/preview?purpose=chat` (F4 FR24, AC2; plan T2.12). No HTTP.

Generated from the catalog, so the list of what can be sent cannot drift from what the loop
sends: every fenced field of every tool, at the data level that sends it, plus the question,
the conversation's history and the names nexovue states. Each free-text kind carries one
sample **as the provider would see it**: F4's send-time masks applied, cut by `bounded` and
fenced by F3's `Untrusted`, exactly as the worker builds a call. A real one where the project
has one; with none, a labelled sample, and consent is still allowed (AC2).

It also states the project's real masking: which default rules its owner switched off, read
from `masking_config`, because a fixed sentence could be false. Runbooks and notes do not
exist before plan Step 5, so nothing is sent for embedding yet, and it says so (0).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform.ai.chat import provider_label
from nexovue_platform.ai.untrusted import Untrusted
from nexovue_platform.assistant_masks import masked_value
from nexovue_platform.assistant_records import CHAT_DISCLOSURE_VERSION, MAX_QUESTION_CHARACTERS
from nexovue_platform.assistant_tools import CATALOG, FENCED_KINDS, SEARCH_LOGS, ChatTool, bounded
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.ai import queries as ai_queries
from nexovue_api.ai.service import NO_CONNECTION, consent_fingerprint_of
from nexovue_api.assistant.schemas import ChatPreview, PreviewKind, PreviewSample
from nexovue_api.assistant.standing import settings_of
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errors.exceptions import ConflictError, NotFoundError
from nexovue_api.logs.search import LogFilters, search_logs
from nexovue_api.projects.config import disabled_rules

# How far back the preview looks for a real log line (a guess: a day is enough to find one).
SAMPLE_LOOKBACK: Final = timedelta(days=1)
SAMPLE_QUESTION: Final = "What is failing right now?"
SAMPLE_LOG_ROW: Final = {
    "observed_at": "2026-11-12T09:41:07Z",
    "service_name": "checkout",
    "severity_number": 17,
    "body": "payment declined for order 1042: Bearer abcdefgh12345678 rejected",
    "attributes": {"customer": "ada@example.com"},
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "row_key": "sample",
}
WHAT_IS_STATED: Final = (
    PreviewKind(kind="Your question", source="the person asking", level="facts"),
    PreviewKind(
        kind="Earlier questions and checked answers in the conversation",
        source="the person asking, and the model's checked words",
        level="facts",
    ),
    PreviewKind(kind="The project's name", source="your team", level="facts"),
    PreviewKind(kind="The service in view", source="your telemetry", level="facts"),
    PreviewKind(
        kind="Runbook and note text, for search",
        source="your team (none exist yet; nothing is sent)",
        level="facts_and_text",
    ),
)


def _kinds() -> list[PreviewKind]:
    """Everything that can be sent, from the catalog: each fenced field, at its level."""
    from_catalog = [
        PreviewKind(
            kind=f"{tool.step_words}: {name.replace('_', ' ')}",
            source="your telemetry" if rule.basis != "verified" else "nexovue's own detection",
            level="facts_and_text" if tool.drops_at_facts_level else "facts",
        )
        for tool in CATALOG
        for name, rule in tool.fields.items()
        if rule.kind in FENCED_KINDS
    ]
    return [*WHAT_IS_STATED, *from_catalog]


def _as_sent(tool: ChatTool, row: dict[str, object], *, mask_emails: bool) -> str:
    """One result row as the worker sends it: catalog fields only, masked, cut, fenced."""
    kept = {
        name: masked_value(row.get(name), mask_emails=mask_emails)
        for name in tool.fields
        if name in row
    }
    text = bounded(
        [kept],
        subject=tool.rows_at,
        untrusted=tool.untrusted_paths(),
        max_rows=tool.max_rows,
        max_characters=tool.max_characters,
    )
    return Untrusted("step s1 result", text).render()


async def chat_preview(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    now: datetime,
) -> ChatPreview:
    connection = await ai_queries.connection_of(session, org_id=org_id)
    if connection is None:
        raise ConflictError(NO_CONNECTION)
    project = await ai_queries.live_project(session, org_id=org_id, project_id=project_id)
    if project is None:
        raise NotFoundError("No such project.")
    settings = await settings_of(session, org_id=org_id)
    mask_emails = True if settings is None else settings.mask_emails
    found = await search_logs(
        repository,
        project_id=str(project_id),
        from_time=now - SAMPLE_LOOKBACK,
        to_time=now,
        filters=LogFilters(),
        limit=1,
        cursor=None,
    )
    newest = found.logs[0].model_dump(mode="json") if found.logs else None
    log_row = newest or SAMPLE_LOG_ROW
    service = str(log_row.get("service_name") or SAMPLE_LOG_ROW["service_name"])
    samples = [
        PreviewSample(
            kind="Your question",
            labelled_sample=True,
            sent=Untrusted("question", SAMPLE_QUESTION[:MAX_QUESTION_CHARACTERS]).render(),
        ),
        PreviewSample(
            kind="The project's name",
            labelled_sample=False,
            sent=Untrusted("project name", project.name).render(),
        ),
        PreviewSample(
            kind="The service in view",
            labelled_sample=newest is None,
            sent=Untrusted("service in view", service).render(),
        ),
        PreviewSample(
            kind=f"{SEARCH_LOGS.step_words}: a log line",
            labelled_sample=newest is None,
            sent=_as_sent(SEARCH_LOGS, log_row, mask_emails=mask_emails),
        ),
    ]
    return ChatPreview(
        provider_label=provider_label(connection.wire_format, connection.base_url),
        address=connection.base_url,
        wire_format=connection.wire_format,
        model=connection.model,
        project_id=project_id,
        project_name=project.name,
        kinds=_kinds(),
        samples=samples,
        masking_rules_off=sorted(disabled_rules(project.masking_config or {})),
        mask_emails=mask_emails,
        sent_for_embedding=0,
        consent_fingerprint=consent_fingerprint_of(connection),
        disclosure_version=CHAT_DISCLOSURE_VERSION,
    )
