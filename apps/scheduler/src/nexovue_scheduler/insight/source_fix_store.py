"""What the AI role reads and writes of a suggestion from a repository's files, as its own
account may (F5 design, *The flow*, step 5, *Grants*; plan T4.2, T4.4, T4.13).

Every statement binds `org_id`. Row security (migrations 0077, 0079) shows this account a
row in `proposing` or an end state, lets it change one only while `proposing`, and shows
it a pack only while its suggestion is `proposing`. So:

- **the pack is deleted first, and the count asserted** (design, step 5), through
  `fix_drop_pack` (ADR-091): a delete after the state moved would match nothing and leave
  whole files behind;
- the only way back to `gathering` is `fix_request_more_files`, which this account may
  execute and nothing else may (D75);
- it reads `model_input` alone of the explanation, never the evidence or the names, and
  of a consent only what FR8 compares. It never reads `edits`, `files_read` or a person.

The reservation, the run row and its settling are F3's own (`store.reserve_month`,
`store.insert_run`, `store.settle_run`), never a second copy: a run F3's sweep had already
called `died` settles nothing and writes nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from nexovue_platform.fixes.records import NOT_IN_THESE_FILES, REFUSED
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.insight import store

_FIX: Final = text(
    """
    SELECT id, org_id, project_id, finding_id, kind, explanation_version, state, folder,
           connection_repository_id, round, retry_code
    FROM finding_fixes
    WHERE org_id = :org_id AND id = :fix_id
    """
)
_PACK: Final = text(
    """
    SELECT files, frames, hint FROM finding_fix_source_packs
    WHERE org_id = :org_id AND fix_id = :fix_id
    """
)
# FR8: the live consents for the row's repository. Which one covers the folder, and whether
# it names the connection as it is now, is worked out in code (`fixes.consent`).
_CONSENTS: Final = text(
    """
    SELECT provider_base_url, provider_format, key_id, model, folder_scope,
           extra_excluded_paths
    FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    """
)
# ADR-091: through the one function, which deletes a pack only while `proposing`.
_DROP_PACK: Final = text("SELECT fix_drop_pack(CAST(:fix_id AS uuid), CAST(:org_id AS uuid))")
_END: Final = text(
    """
    UPDATE finding_fixes
    SET state = :state, reason = :reason, edits = CAST(:edits AS jsonb),
        evidence_ids = CAST(:evidence_ids AS jsonb),
        withheld_refs = CAST(:withheld_refs AS jsonb), tokens_used = tokens_used + :tokens,
        state_changed_at = :at, finished_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    """
)
_KEEP_FOR_RETRY: Final = text(
    """
    UPDATE finding_fixes
    SET retry_code = CAST(:retry_code AS text), tokens_used = tokens_used + :tokens
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    """
)
# D75: the names are stored, then the one function moves the row back to `gathering`,
# raises the round, extends the pack and enqueues the pack job, in this transaction.
_ASK_FOR_FILES: Final = text(
    """
    UPDATE finding_fixes
    SET requested_paths = CAST(:requested AS jsonb), tokens_used = tokens_used + :tokens,
        retry_code = NULL
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposing'
    """
)
_REQUEST_MORE_FILES: Final = text(
    "SELECT fix_request_more_files(CAST(:fix_id AS uuid), CAST(:org_id AS uuid))"
)

Written = Literal["written", "not_written", "already_ended"]


@dataclass(frozen=True, slots=True)
class SourceFix:
    """A suggestion as the AI role may read it: no files read, no edits, no person."""

    id: str
    org_id: str
    project_id: str
    finding_id: str
    kind: str
    explanation_version: int | None
    state: str
    folder: str | None
    repository_id: str | None
    round: int
    retry_code: str | None


@dataclass(frozen=True, slots=True)
class Pack:
    files: list[dict[str, Any]]
    frames: list[dict[str, Any]]
    hint: str | None


@dataclass(frozen=True, slots=True)
class StoredConsent:
    base_url: str
    wire_format: str
    key_id: str | None
    model: str
    folder_scope: str | None
    extra_excluded_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceEnd:
    """Where a call leaves the row: an end, a retry (`state` None), or more files."""

    state: str | None
    reason: str | None = None
    edits: tuple[dict[str, Any], ...] | None = None
    evidence_ids: tuple[str, ...] | None = None
    withheld_refs: tuple[str, ...] | None = None
    retry_code: str | None = None
    asked: tuple[dict[str, Any], ...] | None = None


async def load_fix(engine: AsyncEngine, *, org_id: str, fix_id: str) -> SourceFix | None:
    async with engine.connect() as connection:
        row = (await connection.execute(_FIX, {"org_id": org_id, "fix_id": fix_id})).one_or_none()
    if row is None:
        return None
    return SourceFix(
        id=str(row.id),
        org_id=str(row.org_id),
        project_id=str(row.project_id),
        finding_id=str(row.finding_id),
        kind=row.kind,
        explanation_version=row.explanation_version,
        state=row.state,
        folder=row.folder,
        repository_id=None
        if row.connection_repository_id is None
        else str(row.connection_repository_id),
        round=row.round,
        retry_code=row.retry_code,
    )


async def load_pack(engine: AsyncEngine, fix: SourceFix) -> Pack | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(_PACK, {"org_id": fix.org_id, "fix_id": fix.id})
        ).one_or_none()
    if row is None or not isinstance(row.files, list) or not isinstance(row.frames, list):
        return None
    return Pack(files=row.files, frames=row.frames, hint=row.hint)


async def live_consents(engine: AsyncEngine, fix: SourceFix) -> list[StoredConsent]:
    if fix.repository_id is None:
        return []
    async with engine.connect() as connection:
        rows = await connection.execute(
            _CONSENTS, {"org_id": fix.org_id, "repository_id": fix.repository_id}
        )
        return [
            StoredConsent(
                base_url=row.provider_base_url,
                wire_format=row.provider_format,
                key_id=None if row.key_id is None else str(row.key_id),
                model=row.model,
                folder_scope=row.folder_scope,
                extra_excluded_paths=tuple(
                    str(each) for each in row.extra_excluded_paths or [] if isinstance(each, str)
                ),
            )
            for row in rows
        ]


async def end_without_call(
    engine: AsyncEngine, fix: SourceFix, end: SourceEnd, *, at: datetime
) -> bool:
    """An end decided before anything was sent: the pack first, then the row. False when
    the row had moved (the pack's policy then matched nothing either)."""
    async with engine.begin() as connection:
        written = await _end(connection, fix, end, tokens=0, at=at)
        if not written:
            await connection.rollback()
    return written


async def finish(
    engine: AsyncEngine,
    fix: SourceFix,
    *,
    run_id: str,
    month: date,
    reserved: int,
    run_end: store.RunEnd,
    end: SourceEnd,
) -> Written:
    """A call's end in one transaction: F3's settling first, then the row ended (pack
    deleted first), kept for its retry, or sent back for the files the model asked for."""
    async with engine.begin() as connection:
        if not await store.settle_run(
            connection,
            org_id=fix.org_id,
            run_id=run_id,
            month=month,
            reserved=reserved,
            end=run_end,
        ):
            return "already_ended"
        ids = {"org_id": fix.org_id, "fix_id": fix.id}
        if end.asked is not None:
            asked = await connection.execute(
                _ASK_FOR_FILES,
                {**ids, "requested": json.dumps(list(end.asked)), "tokens": run_end.tokens},
            )
            if not asked.rowcount:
                return "not_written"
            if await connection.scalar(_REQUEST_MORE_FILES, ids):
                return "written"
            # Only a row already in its last round refuses (D74): the model may not ask
            # there, so its answer reads as `not_in_these_files`.
            last = SourceEnd(REFUSED, NOT_IN_THESE_FILES)
            written = await _end(connection, fix, last, tokens=0, at=run_end.finished_at)
            return "written" if written else "not_written"
        if end.state is None:
            kept = await connection.execute(
                _KEEP_FOR_RETRY,
                {**ids, "retry_code": end.retry_code, "tokens": run_end.tokens},
            )
            return "written" if kept.rowcount else "not_written"
        written = await _end(connection, fix, end, tokens=run_end.tokens, at=run_end.finished_at)
    return "written" if written else "not_written"


async def _end(
    connection: AsyncConnection, fix: SourceFix, end: SourceEnd, *, tokens: int, at: datetime
) -> bool:
    """The pack deleted first, its count asserted, then the row's end (design, step 5)."""
    ids = {"org_id": fix.org_id, "fix_id": fix.id}
    if await connection.scalar(_DROP_PACK, ids) != 1:
        return False
    changed = await connection.execute(
        _END,
        {
            **ids,
            "state": end.state,
            "reason": end.reason,
            "edits": None if end.edits is None else json.dumps(list(end.edits)),
            "evidence_ids": None
            if end.evidence_ids is None
            else json.dumps(list(end.evidence_ids)),
            "withheld_refs": None
            if end.withheld_refs is None
            else json.dumps(list(end.withheld_refs)),
            "tokens": tokens,
            "at": at,
        },
    )
    return bool(changed.rowcount)
