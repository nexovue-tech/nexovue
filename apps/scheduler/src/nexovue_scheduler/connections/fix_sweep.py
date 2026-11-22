"""`fixes.sweep_fixes`: rung 3's sweep, on `scheduler-connections` (F5 design, *The sweep*;
plan T3.13).

Beat adds it to `connections` every minute while connections are on. Its own task and
queue, never `jobs/expiry.py`'s loop, so a failing pass here can stop neither retention nor
an alert (AC33). It lives apart from rung 2b's sweep (`fixes/sweep.py`, on `light`), so a
detection or alert worker never loads it (AC6b). Each pass is its own transaction, across
every organization, each row written by its own `org_id`:

1. **Stuck:** a source row still `gathering` past the pack job's life and limit is
   `failed`, `timed_out`; a `gathered` list nobody sent within 15 minutes is `failed`,
   `lapsed` (FR13a, AC46). Candidates and the pack go with the move (AC14).
2. **Ended short, once:** a refused or failed source row's stored code is deleted through
   the one statement (BR9's first row), and `result_recorded_at` is set in the same
   transaction, so a second pass writes nothing twice.
3. **AI off, or 30 days:** stored code is deleted when the organization switched AI off
   or removed its connection, and when a written suggestion is 30 days old (BR9).
4. **Short-lived rows** past `expires_at`: packs, check runs, GitHub link attempts.
5. **The Fix App's hourly refresh** of its repositories and grant, as a job each.
6. **GitHub access gone for 72 hours** (BR9; Step 5): a repository the refresh has found
   unreachable that long, or every repository of an App GitHub says is not installed that
   long, has its consents withdrawn (`access_gone`), its work in flight ended and its
   stored code deleted, as an owner's withdrawal would, audited.

Step 4 (T4.8) adds a `proposing` row past every attempt of its job (`failed`,
`timed_out`), and records each new result once, in the transaction that marks it recorded:
a `proposed` row has the pure rules run again first (D18's tamper signal: a refusal there
is `failed`, `checks_refused_at_click`, audited), then the timeline's `fix_proposed`; a
rule's refusal writes `fix.withheld` with the rule and the files it named, resolved
through `files_read`; and every row the requester must hear about owes one email
(`email_due`, D46), which `fixes.notify_requesters` sends from `light`.

Step 7 (T7.5, AC26a): an `approved` row whose draft job did not finish within its expiry,
hard limit and a margin goes back to `proposed`, `did_not_finish`, so **Try again** works; a
branch it stored keeps the one-draft hold until the poller (Step 8) settles it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.cases import (
    RECORD_CASE_AUDIT_SQL,
    RECORD_FINDING_EVENT_SQL,
    case_audit_parameters,
    finding_event_parameters,
)
from nexovue_platform.connections import REPOSITORY_REFRESH
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.records import (
    ACCESS_GONE_AFTER,
    AI_SOURCE_DISCLOSURE_WITHDRAWN,
    APPROVED_STUCK_AFTER,
    CALL_SECONDS_LONGEST,
    CANCELLED,
    CHECKS_REFUSED_AT_CLICK,
    CODE_ACCESS_GONE,
    CONSENT_WITHDRAWN_ACCESS_GONE,
    FAILED,
    FIX_CHECKS_REFUSED,
    FIX_WITHHELD,
    GATHERED_LAPSES_AFTER,
    GATHERING_STUCK_AFTER,
    INTERACTIVE_QUEUE,
    LAPSED,
    PROPOSED,
    REFRESH_FIX_APP_TASK,
    REPOSITORY_AUDIT_TARGET,
    SWEEP_FIXES_TASK,
    WITHHELD_RULES,
    proposing_lifetime,
)
from nexovue_platform.fixes.statements import CLEAR_STORED_CODE
from nexovue_platform.fixes.stored import StoredRefusal, read_stored
from nexovue_platform.jobs import ENQUEUE_UNLESS_WAITING, JobRequest
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine

logger = get_logger(__name__)

BATCH: Final = 200
# BR9: written code lasts at most this long (a guess).
CODE_KEPT_FOR: Final = timedelta(days=30)
REFRESH_EXPIRES_SECONDS: Final = 60
# A sent row past every attempt of its job, at F3's longest call, with the lease's grace:
# no attempt can still write (design, *The sweep*, step 1).
PROPOSING_STUCK_AFTER: Final = proposing_lifetime(CALL_SECONDS_LONGEST)
# Reasons a requester chose, or never confirmed: no email (D46).
NEUTRAL: Final = frozenset({CANCELLED, LAPSED})

# Across every organization: rows nothing will write any more. Each carries its `org_id`.
_ENDED_WAITING: Final = text(
    """
    UPDATE finding_fixes AS fix
    SET state = 'failed', reason = :reason, state_changed_at = :at, finished_at = :at,
        path_candidates = NULL, requested_paths = NULL
    WHERE fix.id IN (
        SELECT waiting.id FROM finding_fixes AS waiting
        WHERE waiting.kind = 'source' AND waiting.state = :state
          AND waiting.state_changed_at < :before
          AND (CAST(:only_org AS uuid) IS NULL OR waiting.org_id = CAST(:only_org AS uuid))
        ORDER BY waiting.state_changed_at
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING fix.org_id, fix.id
    """
)
_DROP_PACK: Final = text(
    "DELETE FROM finding_fix_source_packs WHERE org_id = :org_id AND fix_id = :fix_id"
)
# Across every organization: drafts whose job never finished (AC26a). A late retry then
# finds the state moved and stops.
_DRAFTS_DID_NOT_FINISH: Final = text(
    """
    UPDATE finding_fixes AS fix
    SET state = 'proposed', state_changed_at = :at, last_write_failure = 'did_not_finish'
    WHERE fix.id IN (
        SELECT waiting.id FROM finding_fixes AS waiting
        WHERE waiting.state = 'approved' AND waiting.state_changed_at < :before
          AND (CAST(:only_org AS uuid) IS NULL OR waiting.org_id = CAST(:only_org AS uuid))
        ORDER BY waiting.state_changed_at
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING fix.org_id, fix.id
    """
)
# Across every organization: source rows that ended, whose result is not yet recorded.
_UNRECORDED: Final = text(
    """
    SELECT id, org_id, finding_id, state, reason, edits, files_read, folder, withheld_refs,
           explanation_version, connection_repository_id, checks_version, notify_requester,
           sent_at, finished_at
    FROM finding_fixes
    WHERE kind = 'source' AND state IN ('proposed', 'refused', 'failed')
      AND result_recorded_at IS NULL
      AND (CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid))
    ORDER BY state_changed_at
    LIMIT :batch
    FOR UPDATE SKIP LOCKED
    """
)
# The row's result marked recorded, and whether its requester is owed an email (D46).
_RECORDED: Final = text(
    """
    UPDATE finding_fixes SET result_recorded_at = :at, email_due = :email_due
    WHERE org_id = :org_id AND id = :fix_id AND result_recorded_at IS NULL
    """
)
_REFUSED_AT_RECORD: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'failed', reason = 'checks_refused_at_click', state_changed_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposed'
    """
)
_EXPLANATION: Final = text(
    """
    SELECT model_input, aliases FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id AND version = :version
    """
)
_UNWITHDRAWN: Final = text(
    """
    SELECT folder_scope, extra_excluded_paths FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    """
)
# Across every organization: code kept where AI was switched off by a person or removed,
# and code past its 30 days (BR9).
_CODE_DUE: Final = text(
    """
    SELECT fix.id, fix.org_id,
           CASE WHEN ai.id IS NULL OR (NOT ai.enabled AND ai.off_reason = 'person')
                THEN 'ai_off' ELSE 'aged_out' END AS reason
    FROM finding_fixes AS fix
    LEFT JOIN ai_connections AS ai ON ai.org_id = fix.org_id
    WHERE fix.kind = 'source' AND fix.code_deleted_at IS NULL
      AND fix.state NOT IN ('gathering', 'gathered', 'proposing')
      AND (fix.files_read IS NOT NULL OR fix.edits IS NOT NULL)
      AND (ai.id IS NULL OR (NOT ai.enabled AND ai.off_reason = 'person')
           OR fix.finished_at < :aged_before)
      AND (CAST(:only_org AS uuid) IS NULL OR fix.org_id = CAST(:only_org AS uuid))
    ORDER BY fix.state_changed_at
    LIMIT :batch
    """
)
_EXPIRED_PACKS: Final = text(
    "DELETE FROM finding_fix_source_packs WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
_EXPIRED_CHECKS: Final = text(
    "DELETE FROM finding_fix_checks WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
_EXPIRED_ATTEMPTS: Final = text(
    "DELETE FROM github_link_attempts WHERE expires_at < :at AND "
    "(CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid)) RETURNING org_id"
)
# Across every organization: repositories with a live consent whose GitHub access has been
# gone past the limit (BR9): unreachable since a refresh first missed it, or an App GitHub
# says is not installed (F22's standing, `needs_attention`).
_ACCESS_GONE: Final = text(
    """
    SELECT repository.org_id, repository.id AS repository_id
    FROM connection_repositories AS repository
    JOIN connections AS app
      ON app.org_id = repository.org_id AND app.id = repository.connection_id
    WHERE app.kind = 'github_fix' AND app.state <> 'disconnected'
      AND ((NOT repository.is_reachable AND repository.unreachable_since < :before)
           OR (app.state = 'needs_attention' AND app.last_failure = 'app_not_installed'
               AND app.failing_since < :before))
      AND EXISTS (
          SELECT 1 FROM fix_source_consents AS consent
          WHERE consent.org_id = repository.org_id
            AND consent.connection_repository_id = repository.id
            AND consent.withdrawn_at IS NULL)
      AND (CAST(:only_org AS uuid) IS NULL OR repository.org_id = CAST(:only_org AS uuid))
    ORDER BY repository.id
    LIMIT :batch
    """
)
_WITHDRAW_FOR_ACCESS: Final = text(
    """
    UPDATE fix_source_consents
    SET withdrawn_at = :at, withdrawn_reason = 'access_gone'
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    RETURNING id, folder_scope
    """
)
_REPOSITORY_CODE: Final = text(
    """
    SELECT id FROM finding_fixes
    WHERE org_id = :org_id AND kind = 'source' AND connection_repository_id = :repository_id
      AND code_deleted_at IS NULL
      AND (files_read IS NOT NULL OR edits IS NOT NULL
           OR state IN ('gathering', 'gathered', 'proposing'))
    ORDER BY id
    FOR UPDATE
    """
)
_END_IN_FLIGHT: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'failed', reason = 'consent_ended_in_flight', state_changed_at = :at,
        finished_at = :at, path_candidates = NULL, requested_paths = NULL
    WHERE org_id = :org_id AND id = :fix_id
      AND state IN ('gathering', 'gathered', 'proposing')
    """
)
_APPS_DUE: Final = text(
    """
    SELECT id, org_id FROM connections
    WHERE kind = 'github_fix' AND state <> 'disconnected' AND github_app_id IS NOT NULL
      AND sealed_credential IS NOT NULL
      AND (repositories_read_at IS NULL OR repositories_read_at < :refresh_before)
      AND (CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid))
    ORDER BY id
    LIMIT :batch
    """
)


@dataclass(frozen=True, slots=True)
class SweptFixes:
    ended: int
    cleared: int
    expired: int
    refreshes: int
    access_gone: int = 0


async def sweep(
    engine: AsyncEngine, *, clock: Callable[[], datetime], only_org: str | None = None
) -> SweptFixes:
    """Every organization's due rows; `only_org` narrows each pass to one, for a test,
    so a test's sweep never reaches another test's rows."""
    now = clock()
    ended = 0
    for state, after, reason in (
        ("gathering", GATHERING_STUCK_AFTER, "timed_out"),
        ("gathered", GATHERED_LAPSES_AFTER, "lapsed"),
        ("proposing", PROPOSING_STUCK_AFTER, "timed_out"),
    ):
        async with engine.begin() as connection:
            rows = (
                await connection.execute(
                    _ENDED_WAITING,
                    {
                        "state": state,
                        "before": now - after,
                        "reason": reason,
                        "at": now,
                        "batch": BATCH,
                        "only_org": only_org,
                    },
                )
            ).all()
            for row in rows:
                await connection.execute(_DROP_PACK, {"org_id": row.org_id, "fix_id": row.id})
        ended += len(rows)
    async with engine.begin() as connection:
        returned = (
            await connection.execute(
                _DRAFTS_DID_NOT_FINISH,
                {
                    "before": now - APPROVED_STUCK_AFTER,
                    "at": now,
                    "batch": BATCH,
                    "only_org": only_org,
                },
            )
        ).all()
    ended += len(returned)
    cleared = await _record_results(engine, now=now, only_org=only_org)
    cleared += await _clear_due(engine, now=now, only_org=only_org)
    async with engine.begin() as connection:
        expired = 0
        for statement in (_EXPIRED_PACKS, _EXPIRED_CHECKS, _EXPIRED_ATTEMPTS):
            expired += len(
                (await connection.execute(statement, {"at": now, "only_org": only_org})).all()
            )
    refreshes = await _queue_refreshes(engine, now=now, only_org=only_org)
    access_gone = await _take_back_lost_access(engine, now=now, only_org=only_org)
    return SweptFixes(
        ended=ended,
        cleared=cleared,
        expired=expired,
        refreshes=refreshes,
        access_gone=access_gone,
    )


async def _record_results(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    """Each ended source row's result, once (design, *The sweep*, step 3). Returns how many
    rows had their stored code deleted: every row that did not end with a suggestion."""
    cleared = 0
    async with engine.begin() as connection:
        rows = (await connection.execute(_UNRECORDED, {"batch": BATCH, "only_org": only_org})).all()
        for row in rows:
            ids = {"org_id": row.org_id, "fix_id": row.id}
            state, reason = row.state, row.reason
            if state == PROPOSED and await _refused_again(connection, row):
                await connection.execute(_REFUSED_AT_RECORD, {**ids, "at": now})
                await _audit(connection, row, FIX_CHECKS_REFUSED, {"fix_id": str(row.id)})
                state, reason = FAILED, CHECKS_REFUSED_AT_CLICK
            if state == PROPOSED:
                await connection.execute(
                    text(RECORD_FINDING_EVENT_SQL),
                    finding_event_parameters(
                        "fix_proposed",
                        org_id=row.org_id,
                        finding_id=row.finding_id,
                        actor_kind="system",
                        at=row.finished_at or now,
                        fix_id=row.id,
                        fix_kind="source",
                        step_count=len(_changed_refs(row.edits)),
                    ),
                )
            else:
                if reason in WITHHELD_RULES:
                    await _audit(connection, row, FIX_WITHHELD, _withheld_detail(row, reason))
                await connection.execute(text(CLEAR_STORED_CODE), {**ids, "reason": "ended_short"})
                cleared += 1
            await connection.execute(
                _RECORDED, {**ids, "at": now, "email_due": _owes_email(row, reason)}
            )
    return cleared


async def _refused_again(connection: AsyncConnection, row: Any) -> bool:
    """D18: the pure rules run again on what the AI room stored, before anyone is told. A
    row at an older `checks_version` is only ever "Out of date", never refused here."""
    if row.checks_version != CHECKS_VERSION:
        return False
    model_input, book = None, None
    if row.explanation_version is not None:
        found = (
            await connection.execute(
                _EXPLANATION,
                {
                    "org_id": row.org_id,
                    "finding_id": row.finding_id,
                    "version": row.explanation_version,
                },
            )
        ).one_or_none()
        if found is not None:
            model_input, book = found.model_input, AliasBook(found.aliases)
    extra: list[str] = []
    if row.connection_repository_id is not None:
        consents = await connection.execute(
            _UNWITHDRAWN, {"org_id": row.org_id, "repository_id": row.connection_repository_id}
        )
        extra = [
            path
            for consent in consents
            if folder_covered(consent.folder_scope, row.folder)
            for path in consent.extra_excluded_paths or []
        ]
    checked = read_stored(
        row.edits,
        row.files_read,
        folder=row.folder,
        owner=OwnerLists(extra_excluded_paths=tuple(extra)),
        allowed_evidence=_allowed_evidence(model_input, row.files_read),
        aliases=book,
    )
    return isinstance(checked, StoredRefusal)


def _allowed_evidence(model_input: Any, files_read: Any) -> set[str]:
    if isinstance(model_input, dict):
        return {
            str(fact["id"])
            for fact in model_input.get("facts") or []
            if isinstance(fact, dict) and "id" in fact
        }
    return {
        str(each.get("ref"))
        for each in (files_read or {}).get("files") or []
        if isinstance(each, dict)
    }


def _changed_refs(edits: Any) -> set[str]:
    return {str(each.get("file_ref")) for each in edits or [] if isinstance(each, dict)}


def _withheld_detail(row: Any, reason: str) -> dict[str, Any]:
    """The rule and the files it named, resolved through `files_read`, which only this
    room writes: the AI room's references alone are never trusted to name a path."""
    paths = {
        str(each.get("ref")): str(each.get("path"))
        for each in (row.files_read or {}).get("files") or []
        if isinstance(each, dict)
    }
    refs = [str(ref) for ref in row.withheld_refs or [] if isinstance(ref, str)]
    return {
        "fix_id": str(row.id),
        "rule": reason.removeprefix("withheld_"),
        "files": [paths[ref] for ref in refs if ref in paths],
    }


def _owes_email(row: Any, reason: str | None) -> bool:
    """D46: the requester hears the result of what they sent, unless they cleared the box.
    A cancel, a lapse or a row that never sent anything owes nothing."""
    return bool(row.notify_requester) and row.sent_at is not None and reason not in NEUTRAL


async def _audit(
    connection: AsyncConnection, row: Any, action: str, detail: dict[str, Any]
) -> None:
    await connection.execute(
        text(RECORD_CASE_AUDIT_SQL),
        case_audit_parameters(
            org_id=row.org_id,
            actor_user_id=None,
            action=action,
            target=f"{FINDING_AUDIT_TARGET}:{row.finding_id}",
            detail=detail,
        ),
    )


async def _clear_due(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                _CODE_DUE,
                {"aged_before": now - CODE_KEPT_FOR, "batch": BATCH, "only_org": only_org},
            )
        ).all()
        for row in rows:
            await connection.execute(
                text(CLEAR_STORED_CODE),
                {"org_id": row.org_id, "fix_id": row.id, "reason": row.reason},
            )
    return len(rows)


async def _queue_refreshes(engine: AsyncEngine, *, now: datetime, only_org: str | None) -> int:
    async with engine.begin() as connection:
        due = (
            await connection.execute(
                _APPS_DUE,
                {
                    "refresh_before": now - REPOSITORY_REFRESH,
                    "batch": BATCH,
                    "only_org": only_org,
                },
            )
        ).all()
        for row in due:
            request = JobRequest(
                task=REFRESH_FIX_APP_TASK,
                queue=INTERACTIVE_QUEUE,
                kwargs={"org_id": str(row.org_id), "connection_id": str(row.id)},
                org_id=str(row.org_id),
                expires_seconds=REFRESH_EXPIRES_SECONDS,
            )
            await connection.execute(text(ENQUEUE_UNLESS_WAITING), request.parameters())
    return len(due)


async def _take_back_lost_access(
    engine: AsyncEngine, *, now: datetime, only_org: str | None
) -> int:
    """Step 6: each repository in its own transaction, consents first, then the rows, the
    same order an owner's withdrawal takes its locks in."""
    async with engine.connect() as connection:
        due = (
            await connection.execute(
                _ACCESS_GONE,
                {"before": now - ACCESS_GONE_AFTER, "batch": BATCH, "only_org": only_org},
            )
        ).all()
    for repository in due:
        ids = {"org_id": repository.org_id, "repository_id": repository.repository_id}
        async with engine.begin() as connection:
            withdrawn = (await connection.execute(_WITHDRAW_FOR_ACCESS, {**ids, "at": now})).all()
            rows = (await connection.execute(_REPOSITORY_CODE, ids)).all()
            for row in rows:
                each = {"org_id": repository.org_id, "fix_id": row.id}
                await connection.execute(_END_IN_FLIGHT, {**each, "at": now})
                await connection.execute(_DROP_PACK, each)
                await connection.execute(
                    text(CLEAR_STORED_CODE), {**each, "reason": CODE_ACCESS_GONE}
                )
            for consent in withdrawn:
                await connection.execute(
                    text(RECORD_CASE_AUDIT_SQL),
                    case_audit_parameters(
                        org_id=repository.org_id,
                        actor_user_id=None,
                        action=AI_SOURCE_DISCLOSURE_WITHDRAWN,
                        target=f"{REPOSITORY_AUDIT_TARGET}:{repository.repository_id}",
                        detail={
                            "consent_id": str(consent.id),
                            "folder_scope": consent.folder_scope,
                            "reason": CONSENT_WITHDRAWN_ACCESS_GONE,
                            "code_deleted": len(rows),
                        },
                    ),
                )
    return len(due)


@task(SWEEP_FIXES_TASK)
def sweep_fixes_task() -> None:
    swept = with_engine(lambda engine: sweep(engine, clock=lambda: datetime.now(UTC)))
    if swept.ended or swept.cleared or swept.expired or swept.refreshes or swept.access_gone:
        logger.info(
            "the suggested fix sweep moved rows",
            ended=swept.ended,
            cleared=swept.cleared,
            expired=swept.expired,
            refreshes=swept.refreshes,
            access_gone=swept.access_gone,
        )
