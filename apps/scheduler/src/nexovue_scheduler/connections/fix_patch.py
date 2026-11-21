"""A person's check run for a patch (F5 design, *The check run*, steps 3 to 6; spec FR20a,
FR21, AC10a's patch half, AC19, AC24, AC28a, AC29a, AC40, AC55; plan T5.1).

After the check job's step 1, on `scheduler-connections`, with a read-only token only:

3. Under the owner's requirement, GitHub must say the person can read the repository.
4. **Out of date, with no read:** today's rules and the owner's list against those the
   suggestion was written under, this service's code location against the copy on the row,
   and F3's likely cause against the one it started from. Any change moves the suggestion
   to `stale`, with its reason, and no alert.
5. The default branch's head and each changed file there, and each moved file's base blob,
   then one `repository.read` entry naming the person, and the re-check
   (`fixes/recheck.py`): **unchanged**, **moved**, **out of date**, or **refused again**,
   D18's tamper signal, which ends the suggestion `failed`, `checks_refused_at_click`,
   deletes its code (BR9), audits it and warns, so the alert reads it.
6. A passing patch is stored on this person's own check row by a statement conditional on
   the check still `running`, the suggestion still `proposed` with its code, and a consent
   still covering its folder, taken under the suggestion's row lock: an owner's
   withdrawal, which takes that lock to delete code, can never be overtaken (AC29a).

Nothing here writes to GitHub (AC28a). Logs carry ids and codes only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.pack import owner_lists_hash
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.recheck import (
    CurrentFile,
    Outcome,
    OutOfDate,
    Rechecked,
    RefusedAgain,
    recheck,
)
from nexovue_platform.fixes.records import (
    CHECK_FAILED,
    CHECK_FOR_PATCH,
    CHECK_REFUSED,
    CHECK_STALE,
    CHECKS_REFUSED_AT_CLICK,
    CODE_ENDED_SHORT,
    FIX_CHECKS_REFUSED,
    PATCH_CHECK_LIFETIME,
    PROPOSED,
    REPOSITORY_READ,
    STALE,
    STALE_REASONS,
)
from nexovue_platform.fixes.statements import CLEAR_STORED_CODE
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.connections import check_rows
from nexovue_scheduler.connections.fix_github import FixEndpoints, FixGitHub, GitHubFailure
from nexovue_scheduler.connections.source_pack import (
    Ending,
    check_access,
    fix_client,
    folder_files,
)

logger = get_logger(__name__)

# Out of date, in the order the design names them (FR21).
RULES_CHANGED: Final = "rules_changed"
MAPPING_CHANGED: Final = "mapping_changed"
CAUSE_CHANGED: Final = "cause_changed"
# What `blob` refuses to hand back as a file: cut past the client's cap, or gone.
_UNREADABLE: Final = frozenset({"repository_too_large", "not_found"})

# Every statement binds the job's own `org_id`.
_SUGGESTION: Final = text(
    """
    SELECT fix.id, fix.org_id, fix.project_id, fix.finding_id, fix.state, fix.reason,
           fix.code_deleted_at, fix.connection_repository_id, fix.folder, fix.edits,
           fix.files_read, fix.checks_version, fix.extra_list_hash,
           fix.explanation_version, finding.service_name
    FROM finding_fixes AS fix
    JOIN findings AS finding ON finding.org_id = fix.org_id AND finding.id = fix.finding_id
    WHERE fix.org_id = :org_id AND fix.id = :fix_id
    """
)
_CONSENTS: Final = text(
    """
    SELECT folder_scope, extra_excluded_paths, allowed_config_paths, require_github_access
    FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    ORDER BY folder_scope NULLS LAST
    """
)
_LOCATION: Final = text(
    """
    SELECT connection_repository_id, folder FROM service_code_locations
    WHERE org_id = :org_id AND service_name = :service_name
      AND (project_id = :project_id OR project_id IS NULL)
    ORDER BY project_id IS NULL
    LIMIT 1
    """
)
_CAUSE_OF: Final = text(
    """
    SELECT version, likely_cause, model_input, aliases FROM finding_explanations
    WHERE org_id = :org_id AND finding_id = :finding_id
      AND (version = :version OR version = (
          SELECT max(version) FROM finding_explanations
          WHERE org_id = :org_id AND finding_id = :finding_id))
    """
)
_LOCK: Final = text(
    """
    SELECT state, reason, code_deleted_at FROM finding_fixes
    WHERE org_id = :org_id AND id = :fix_id
    FOR UPDATE
    """
)
_STORE_PATCH: Final = text(
    """
    UPDATE finding_fix_checks AS checked
    SET status = :status, reason = NULL, head_sha = :head_sha,
        blob_ids = CAST(:blob_ids AS jsonb), moved_refs = CAST(:moved_refs AS jsonb),
        patch = :patch, digest = :digest, finished_at = :at, expires_at = :expires_at
    WHERE checked.org_id = :org_id AND checked.id = :check_id AND checked.status = 'running'
      AND EXISTS (
          SELECT 1 FROM finding_fixes AS fix
          WHERE fix.org_id = :org_id AND fix.id = checked.fix_id
            AND fix.state = 'proposed' AND fix.code_deleted_at IS NULL)
    RETURNING checked.id
    """
)
_TO_STALE: Final = text(
    """
    UPDATE finding_fixes SET state = 'stale', reason = :reason, state_changed_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposed'
      AND code_deleted_at IS NULL
    RETURNING id
    """
)
_REFUSED_AT_CHECK: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'failed', reason = 'checks_refused_at_click', state_changed_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'proposed'
    RETURNING finding_id
    """
)


@dataclass(frozen=True, slots=True)
class _Reading:
    head_sha: str
    head: dict[str, CurrentFile | None]
    base: dict[str, str | None]
    paths: list[dict[str, str]]


async def check_patch(
    engine: AsyncEngine,
    check: Any,
    row: Any,
    *,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> str | None:
    """Steps 3 to 6 for one `patch` check. Returns the status it left."""
    org_id = str(row.org_id)
    suggestion = await _suggestion(engine, org_id, str(check.fix_id))
    if suggestion is None:
        # Gone with its finding since the click, and this check row with it.
        return None
    ended = _not_proposed(suggestion)
    if ended is not None:
        return await check_rows.finish(engine, org_id, check, *ended, now=now)
    consent = await _covering_consent(engine, suggestion)
    if consent is None:
        return await check_rows.finish(
            engine, org_id, check, CHECK_FAILED, "consent_withdrawn", now=now
        )
    owner, require_access = consent
    try:
        github = fix_client(row, keys, endpoints)
        token = github.read_token(row.github_installation_id, int(row.external_id))
        try:
            if require_access:
                linked = await check_rows.github_user_id(engine, org_id, check)
                check_access(github, token, row.key, linked)
            stale = await _out_of_date(engine, suggestion, owner)
            if stale is not None:
                return await _stale(engine, check, suggestion, stale, now=now)
            reading = _read(github, token, row.key, suggestion)
        finally:
            github.revoke(token)
    except Ending as ending:
        return await check_rows.end_on_refusal(engine, org_id, check, ending, now=now)
    except GitHubFailure as failure:
        return await check_rows.end_on_github_failure(engine, org_id, check, failure, now=now)
    await _audit_read(engine, check, suggestion, reading)
    explanation = await _explanation(engine, suggestion)
    outcome = recheck(
        suggestion.edits,
        suggestion.files_read,
        folder=suggestion.folder,
        owner=owner,
        allowed_evidence=_allowed_evidence(explanation, suggestion.files_read),
        aliases=None if explanation is None else AliasBook(explanation.aliases),
        head=reading.head,
        base=reading.base,
    )
    return await _store(engine, check, suggestion, outcome, reading, now=now)


async def _suggestion(engine: AsyncEngine, org_id: str, fix_id: str) -> Any | None:
    async with engine.connect() as connection:
        return (
            await connection.execute(_SUGGESTION, {"org_id": org_id, "fix_id": fix_id})
        ).one_or_none()


def _not_proposed(suggestion: Any) -> tuple[str, str] | None:
    """A patch is built only from a written suggestion that still holds its code. A row
    already out of date or refused says so on the check; anything else cannot be built."""
    if suggestion.state == PROPOSED and suggestion.code_deleted_at is None:
        return None
    if suggestion.state == STALE and suggestion.reason in STALE_REASONS:
        return CHECK_STALE, str(suggestion.reason)
    if suggestion.reason == CHECKS_REFUSED_AT_CLICK:
        return CHECK_REFUSED, CHECKS_REFUSED_AT_CLICK
    return CHECK_FAILED, "nexovue_error"


async def _covering_consent(engine: AsyncEngine, suggestion: Any) -> tuple[OwnerLists, bool] | None:
    """The unwithdrawn consent the pack job would have chosen for this folder: its owner's
    lists, and whether it asks for GitHub access (D20, any covering consent asking)."""
    async with engine.connect() as connection:
        consents = (
            await connection.execute(
                _CONSENTS,
                {
                    "org_id": suggestion.org_id,
                    "repository_id": suggestion.connection_repository_id,
                },
            )
        ).all()
    covering = [each for each in consents if folder_covered(each.folder_scope, suggestion.folder)]
    if not covering:
        return None
    chosen = covering[0]
    owner = OwnerLists(
        extra_excluded_paths=tuple(chosen.extra_excluded_paths or ()),
        allowed_config_paths=tuple(chosen.allowed_config_paths or ()),
    )
    return owner, any(each.require_github_access for each in covering)


async def _out_of_date(engine: AsyncEngine, suggestion: Any, owner: OwnerLists) -> str | None:
    """Step 5's comparisons that need no file (FR21)."""
    if (
        suggestion.checks_version != CHECKS_VERSION
        or suggestion.extra_list_hash != owner_lists_hash(owner)
    ):
        return RULES_CHANGED
    ids = {"org_id": suggestion.org_id}
    async with engine.connect() as connection:
        location = (
            await connection.execute(
                _LOCATION,
                {
                    **ids,
                    "service_name": suggestion.service_name,
                    "project_id": suggestion.project_id,
                },
            )
        ).one_or_none()
        causes = (
            await connection.execute(
                _CAUSE_OF,
                {
                    **ids,
                    "finding_id": suggestion.finding_id,
                    "version": suggestion.explanation_version,
                },
            )
        ).all()
    if location is None or (
        location.connection_repository_id,
        location.folder or "",
    ) != (suggestion.connection_repository_id, suggestion.folder or ""):
        return MAPPING_CHANGED
    started = next(
        (each for each in causes if each.version == suggestion.explanation_version), None
    )
    latest = max(causes, key=lambda each: each.version, default=None)
    if _cause(started) != _cause(latest):
        return CAUSE_CHANGED
    return None


def _cause(explanation: Any) -> tuple[Any, Any] | None:
    """A likely cause by its stable rule name and the fact it names (F3's ids are stable
    across versions), so a version that changes nothing material does not end it."""
    cause = None if explanation is None else explanation.likely_cause
    return None if not cause else (cause.get("rule"), cause.get("evidence_id"))


def _read(github: FixGitHub, token: str, key: str, suggestion: Any) -> _Reading:
    """The default branch's head, each changed file there, and each moved file's base."""
    listed = {
        str(each["ref"]): each
        for each in (suggestion.files_read or {}).get("files") or []
        if isinstance(each, dict) and isinstance(each.get("ref"), str)
    }
    refs = [
        ref
        for ref in dict.fromkeys(
            str(edit.get("file_ref")) for edit in suggestion.edits or [] if isinstance(edit, dict)
        )
        if ref in listed
    ]
    facts = github.repository(token, key)
    head_sha = github.head_of(token, key, facts.default_branch)
    tree = {
        file.path: file for file in folder_files(github, token, key, suggestion.folder, head_sha)
    }
    head: dict[str, CurrentFile | None] = {}
    base: dict[str, str | None] = {}
    for ref in refs:
        entry = tree.get(str(listed[ref]["path"]))
        if entry is None:
            head[ref] = None
            continue
        head[ref] = CurrentFile(entry.sha, entry.mode, _text_of(github, token, key, entry.sha))
        if entry.sha != listed[ref].get("blob_sha"):
            base[ref] = _text_of(github, token, key, str(listed[ref].get("blob_sha")))
    return _Reading(
        head_sha=head_sha,
        head=head,
        base=base,
        paths=[
            {"path": str(listed[ref]["path"]), "role": str(listed[ref].get("role"))} for ref in refs
        ],
    )


def _text_of(github: FixGitHub, token: str, key: str, sha: str) -> str | None:
    """A file's text, or None when it cannot be one: past the client's 1 MiB (a cut
    answer has no flag, design step 4), gone, a NUL byte, or not UTF-8."""
    try:
        data = github.blob(token, key, sha)
    except GitHubFailure as failure:
        if failure.code in _UNREADABLE:
            return None
        raise
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def _explanation(engine: AsyncEngine, suggestion: Any) -> Any | None:
    if suggestion.explanation_version is None:
        return None
    async with engine.connect() as connection:
        found = (
            await connection.execute(
                _CAUSE_OF,
                {
                    "org_id": suggestion.org_id,
                    "finding_id": suggestion.finding_id,
                    "version": suggestion.explanation_version,
                },
            )
        ).all()
    return next((each for each in found if each.version == suggestion.explanation_version), None)


def _allowed_evidence(explanation: Any | None, files_read: Any) -> set[str]:
    """U3 as at the call: the explanation's fact ids, else the files' numbers."""
    model_input = None if explanation is None else explanation.model_input
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


async def _audit_read(engine: AsyncEngine, check: Any, suggestion: Any, reading: _Reading) -> None:
    """`repository.read`, naming the person and the purpose, with the paths (FR28a)."""
    async with engine.begin() as connection:
        await connection.execute(
            text(RECORD_CASE_AUDIT_SQL),
            case_audit_parameters(
                org_id=suggestion.org_id,
                actor_user_id=check.requested_by,
                action=REPOSITORY_READ,
                target=f"{FINDING_AUDIT_TARGET}:{suggestion.finding_id}",
                detail={
                    "fix_id": str(suggestion.id),
                    "check_id": str(check.id),
                    "purpose": CHECK_FOR_PATCH,
                    "paths": reading.paths,
                },
            ),
        )


async def _store(
    engine: AsyncEngine,
    check: Any,
    suggestion: Any,
    outcome: Outcome,
    reading: _Reading,
    *,
    now: datetime,
) -> str | None:
    if isinstance(outcome, OutOfDate):
        return await _stale(engine, check, suggestion, outcome.reason, now=now)
    if isinstance(outcome, RefusedAgain):
        return await _refused_again(engine, check, suggestion, outcome, now=now)
    return await _passed(engine, check, suggestion, outcome, reading, now=now)


async def _passed(
    engine: AsyncEngine,
    check: Any,
    suggestion: Any,
    outcome: Rechecked,
    reading: _Reading,
    *,
    now: datetime,
) -> str | None:
    """Step 6, under the suggestion's lock, so a withdrawal cannot put source back."""
    ids = {"org_id": suggestion.org_id, "fix_id": suggestion.id}
    async with engine.begin() as connection:
        locked = (await connection.execute(_LOCK, ids)).one_or_none()
        if locked is None:
            return None
        ended = _not_proposed(locked)
        if ended is None and not await _still_covered(connection, suggestion):
            ended = (CHECK_FAILED, "consent_withdrawn")
        if ended is not None:
            return await check_rows.finish_in(
                connection, str(suggestion.org_id), check, *ended, now=now
            )
        stored = (
            await connection.execute(
                _STORE_PATCH,
                {
                    "org_id": suggestion.org_id,
                    "check_id": check.id,
                    "status": outcome.status,
                    "head_sha": reading.head_sha,
                    "blob_ids": json.dumps(outcome.blob_ids),
                    "moved_refs": json.dumps(list(outcome.moved_refs)),
                    "patch": outcome.patch,
                    "digest": outcome.digest,
                    "at": now,
                    "expires_at": now + PATCH_CHECK_LIFETIME,
                },
            )
        ).one_or_none()
    if stored is None:
        return None
    logger.info(
        "a patch check ended",
        check_id=str(check.id),
        status=outcome.status,
        moved=len(outcome.moved_refs),
    )
    return outcome.status


async def _still_covered(connection: AsyncConnection, suggestion: Any) -> bool:
    consents = await connection.execute(
        _CONSENTS,
        {"org_id": suggestion.org_id, "repository_id": suggestion.connection_repository_id},
    )
    return any(folder_covered(each.folder_scope, suggestion.folder) for each in consents)


async def _stale(
    engine: AsyncEngine, check: Any, suggestion: Any, reason: str, *, now: datetime
) -> str | None:
    """Out of date: the suggestion and this check say why, and nothing alerts (AC55)."""
    ids = {"org_id": suggestion.org_id, "fix_id": suggestion.id}
    async with engine.begin() as connection:
        await connection.execute(_LOCK, ids)
        await connection.execute(_TO_STALE, {**ids, "reason": reason, "at": now})
        return await check_rows.finish_in(
            connection, str(suggestion.org_id), check, CHECK_STALE, reason, now=now
        )


async def _refused_again(
    engine: AsyncEngine, check: Any, suggestion: Any, outcome: RefusedAgain, *, now: datetime
) -> str | None:
    """D18's one tamper signal, at a check run: the same rules, list and files gave a
    different result. The suggestion ends, its code goes in the same transaction (BR9's
    first row), the act is audited and a warning logged; `checks_refused_at_click` on the
    row is what the alert counts."""
    ids = {"org_id": suggestion.org_id, "fix_id": suggestion.id}
    async with engine.begin() as connection:
        await connection.execute(_LOCK, ids)
        refused = (await connection.execute(_REFUSED_AT_CHECK, {**ids, "at": now})).one_or_none()
        if refused is None:
            return None
        await connection.execute(
            text(RECORD_CASE_AUDIT_SQL),
            case_audit_parameters(
                org_id=suggestion.org_id,
                actor_user_id=None,
                action=FIX_CHECKS_REFUSED,
                target=f"{FINDING_AUDIT_TARGET}:{refused.finding_id}",
                detail={
                    "fix_id": str(suggestion.id),
                    "check_id": str(check.id),
                    "at": "check_run",
                    "failure": outcome.failure,
                },
            ),
        )
        await connection.execute(text(CLEAR_STORED_CODE), {**ids, "reason": CODE_ENDED_SHORT})
    logger.warning(
        "a written suggestion failed Nexovue's rules again on the same files",
        fix_id=str(suggestion.id),
        failure=outcome.failure,
    )
    return CHECK_REFUSED
