"""F4 T3.2: a chat answer's quotes, read again as the person (design, *Quote display*).

An answer stores a quote as a reference: the step, the tool, the field and the row's own
keys, never its text (D17, D28). Opening the answer reads each one again through the same
services a screen uses, in the person's tenant and the question's project, so a record that
has aged out or been erased reads `gone`, and a failed read reads `read_failed`, never
"gone" (AC19). Postgres finds the message; ClickHouse holds the values.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.assistant.service import Asker
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import AssistantMessage, AssistantStep
from nexovue_api.errorgroups import service as error_groups
from nexovue_api.errors.exceptions import DomainError, NotFoundError
from nexovue_api.logs.search import log_line_at
from nexovue_api.projects.service import require_project

# A quote shows at most this much, and says when it was cut (FR15, a guess).
MAX_QUOTE_CHARACTERS: Final = 500
NOT_FOUND: Final = "That question does not exist."

QuoteState = Literal["shown", "gone", "read_failed"]


class QuoteView(BaseModel):
    id: str
    tool: str
    field: str
    # Folded under "Text from a log, written by anyone who can reach your service. Do not
    # run it." when the worker found a link or a command in it (AC63).
    folded: bool
    state: QuoteState
    value: str | None = None
    was_cut: bool = False


class QuoteList(BaseModel):
    message_id: uuid.UUID
    quotes: list[QuoteView]


class _Gone(Exception):  # noqa: N818 — a record no longer kept, named for the reader
    pass


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def _log_value(
    repository: TenantScopedRepository, project_id: str, field: str, ref: Mapping[str, Any]
) -> object:
    observed = ref.get("observed_at")
    if not isinstance(observed, str):
        raise _Gone
    line = await log_line_at(
        repository,
        project_id=project_id,
        observed_at=datetime.fromisoformat(observed.replace("Z", "+00:00")),
        service_name=str(ref.get("service_name", "")),
        severity_number=int(ref.get("severity_number", 0)),
        row_key=int(str(ref.get("row_key", "0"))),
    )
    if line is None:
        raise _Gone
    return line.body if field == "body" else line.attributes


async def _group_value(
    repository: TenantScopedRepository,
    project_id: str,
    field: str,
    ref: Mapping[str, Any],
    step: AssistantStep | None,
) -> object:
    if step is None or step.from_time is None or step.to_time is None:
        raise _Gone
    try:
        detail = await error_groups.get_error_group(
            repository,
            project_id=project_id,
            group_id=str(ref.get("group_id", "")),
            from_time=step.from_time,
            to_time=step.to_time,
        )
    except NotFoundError as missing:
        raise _Gone from missing
    return getattr(detail.group, field)


async def _read(
    repository: TenantScopedRepository,
    project_id: str,
    stored: Mapping[str, Any],
    step: AssistantStep | None,
) -> object:
    tool = stored.get("tool")
    field = str(stored.get("field", ""))
    ref = stored.get("reference")
    if not isinstance(ref, Mapping):
        raise _Gone
    if tool == "search_logs" and field in ("body", "attributes"):
        return await _log_value(repository, project_id, field, ref)
    if tool in ("list_error_groups", "get_error_group") and field in (
        "message_template",
        "latest_message",
    ):
        return await _group_value(repository, project_id, field, ref, step)
    raise _Gone


async def quotes(
    session: AsyncSession,
    repository: TenantScopedRepository,
    *,
    asker: Asker,
    message_id: uuid.UUID,
) -> QuoteList:
    """Each quote of the person's own answer, read again. Another person's message, or none,
    answers the same 404."""
    message: AssistantMessage | None = await session.scalar(
        select(AssistantMessage).where(
            AssistantMessage.org_id == asker.org_id,
            AssistantMessage.user_id == asker.user_id,
            AssistantMessage.id == message_id,
        )
    )
    if message is None:
        raise NotFoundError(NOT_FOUND)
    project = await require_project(session, org_id=asker.org_id, project_id=message.project_id)
    stored = (message.answer or {}).get("quotes") or {}
    steps = {
        f"s{step.position}": step
        for step in await session.scalars(
            select(AssistantStep).where(
                AssistantStep.org_id == asker.org_id, AssistantStep.message_id == message.id
            )
        )
    }
    views: list[QuoteView] = []
    for quote_id, quote in stored.items() if isinstance(stored, Mapping) else ():
        if not isinstance(quote, Mapping):
            continue
        view = QuoteView(
            id=str(quote_id),
            tool=str(quote.get("tool", "")),
            field=str(quote.get("field", "")),
            folded=bool(quote.get("folded", True)),
            state="gone",
        )
        try:
            value = _text(
                await _read(repository, str(project.id), quote, steps.get(str(quote.get("step"))))
            )
        except _Gone:
            views.append(view)
            continue
        except (DomainError, OSError, ValueError, TimeoutError):
            # A failed read is not "gone": the person may retry (AC19).
            views.append(view.model_copy(update={"state": "read_failed"}))
            continue
        views.append(
            view.model_copy(
                update={
                    "state": "shown",
                    "value": value[:MAX_QUOTE_CHARACTERS],
                    "was_cut": len(value) > MAX_QUOTE_CHARACTERS,
                }
            )
        )
    return QuoteList(message_id=message.id, quotes=views)
