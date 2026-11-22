"""Reading a suggestion's files from GitHub, listing them, and stopping (F5 design, *The
flow*, step 3; *Picking files*; plan T3.10, T3.16, T3.18, T3.19).

`connections.build_source_pack` runs on `scheduler-connections`, which holds the
connection key and **never the AI key** (AC4): nothing here calls a provider. In order:

1. The row must be `gathering`, in the job's own organization; otherwise it stops with no
   call, so the job can safely repeat (AC16a).
2. A live consent must cover the row's folder (FR8): the owner's agreement, unwithdrawn,
   with the AI connection's address, format, key id and model unchanged. Otherwise
   `failed`, `consent_ended_in_flight`, before any call.
3. Under the owner's requirement (D20), the person who asked must be linked and GitHub must
   say they can read the repository, **before any blob is read** (AC10a).
4. A `contents: read` token for this one repository; the default branch's head; F28's
   commit only if GitHub shows it is an ancestor of that head (A17, AC12h).
5. The folder's tree alone; a cut tree is `repository_too_large`, with no blob read and no
   retry (AC16a). Candidates match only files in it; never-sent, non-regular, too-large and
   secret-shaped files are left out and listed (AC12, AC13, AC45).
6. Stack files, then one hop of imports from the top two, then their test files (AC81).
7. In one transaction, only if the row is still `gathering`: the pack, the listing,
   `gathered`, the candidates cleared, and one `repository.read` audit entry naming the
   person (FR28a). A second run finds the state moved and writes nothing (AC16a).

`connections.match_brief_paths` (FR5c) reads the tree alone, and leaves `[{path, line}]`
for the console under a request id: no blob, no consent, nothing leaves.

**Ids, codes and paths only in logs** (design, *Logs*): no content, no token, no message.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from nexovue_platform.cases import RECORD_CASE_AUDIT_SQL, case_audit_parameters
from nexovue_platform.checks.sealing import ConfiguredKeys, SealError
from nexovue_platform.connections import CONNECTION_CREDENTIAL_PURPOSE, connection_secret_owner
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.fixes.consent import ProviderTerms, folder_covered, terms_match
from nexovue_platform.fixes.imports import go_module_of, imported_paths
from nexovue_platform.fixes.matching import TreeIndex, conventional_test_file, match_path
from nexovue_platform.fixes.pack import (
    PackBuilder,
    PackFile,
    TreeFile,
    files_digest,
    owner_lists_hash,
)
from nexovue_platform.fixes.policy import CHECKS_VERSION, OwnerLists
from nexovue_platform.fixes.prose import prose_refusal
from nexovue_platform.fixes.records import (
    BUILD_SOURCE_PACK_TASK,
    CONSENT_ENDED_IN_FLIGHT,
    FAILED,
    FROM_MODEL,
    GATHERED_LAPSES_AFTER,
    GATHERING,
    IMPORTS_FROM_TOP,
    MATCH_BRIEF_PATHS_TASK,
    MAX_ASKED_PATHS,
    MAX_CANDIDATE_CHARACTERS,
    PACK_MAX_ATTEMPTS,
    READ_FOR_BRIEF,
    READ_FOR_FIX,
    REFUSED,
    REPOSITORY_READ,
)
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.connections.fix_github import (
    READ_LEVELS,
    FixEndpoints,
    FixGitHub,
    GitHubFailure,
    RepositoryFacts,
    TreeEntry,
)
from nexovue_scheduler.connections.store import box
from nexovue_scheduler.jobs.registry import Retry, attempt, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# A passing GitHub failure is tried again this much later, while attempts last.
RETRY_SECONDS: Final = 20
# The pack outlives the 15 minutes a gathered list waits, so the sweep's lapse removes it.
PACK_LIFETIME: Final = GATHERED_LAPSES_AFTER + timedelta(minutes=5)
# What the job's failures store (design, *Failure handling*).
_REASON_OF: Final = {
    "permission_missing": "permission_missing",
    "unavailable": "github_unavailable",
    "unexpected": "github_unavailable",
    "not_found": "github_unavailable",
}
# A name the model asked for (FR15b): relative, short, one line. It must then be a path in the
# folder's own listing exactly, so `..` or an address can never name a file.
_ASKED_NAME: Final = re.compile(r"^[^/\x00-\x1f\x7f][^\x00-\x1f\x7f]*$")

# ------------------------------------------------------------------- statements
# Every one binds the job's own `org_id` (`test_scheduler_postgres_tenancy_structure.py`).

_LOAD: Final = text(
    """
    SELECT fix.id, fix.org_id, fix.project_id, fix.finding_id, fix.kind, fix.state,
           fix.folder, fix.connection_repository_id, fix.path_candidates,
           fix.requested_paths, fix.candidates_from, fix.round, fix.read_commit,
           fix.requested_by, fix.files_read, finding.service_name,
           place.external_id, place.key, place.is_reachable,
           connection.id AS connection_id, connection.kind AS connection_kind,
           connection.state AS connection_state, connection.sealed_credential,
           connection.github_app_id, connection.github_installation_id
    FROM finding_fixes AS fix
    JOIN findings AS finding ON finding.org_id = fix.org_id AND finding.id = fix.finding_id
    LEFT JOIN connection_repositories AS place
      ON place.org_id = fix.org_id AND place.id = fix.connection_repository_id
    LEFT JOIN connections AS connection
      ON connection.org_id = place.org_id AND connection.id = place.connection_id
    WHERE fix.org_id = :org_id AND fix.id = :fix_id
    """
)
_PRODUCTION_PATH: Final = text(
    """
    SELECT path_in_production FROM service_code_locations
    WHERE org_id = :org_id AND service_name = :service_name
      AND connection_repository_id = :repository_id
      AND COALESCE(folder, '') = COALESCE(CAST(:folder AS text), '')
      AND (project_id = :project_id OR project_id IS NULL)
    ORDER BY project_id IS NULL
    LIMIT 1
    """
)
_CONSENTS: Final = text(
    """
    SELECT provider_base_url, provider_format, key_id, model, folder_scope,
           extra_excluded_paths, allowed_config_paths, require_github_access
    FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    ORDER BY folder_scope NULLS LAST
    """
)
_AI_CONNECTION: Final = text(
    """
    SELECT base_url, wire_format, key_id, model, enabled FROM ai_connections
    WHERE org_id = :org_id
    """
)
_LINK: Final = text(
    "SELECT github_user_id FROM github_user_links WHERE org_id = :org_id AND user_id = :user_id"
)
_PACK: Final = text(
    """
    SELECT files FROM finding_fix_source_packs WHERE org_id = :org_id AND fix_id = :fix_id
    """
)
_GATHERED: Final = text(
    """
    UPDATE finding_fixes SET
        state = 'gathered', state_changed_at = :at, files_read = CAST(:files_read AS jsonb),
        email_due = notify_requester AND (files_read IS NULL OR candidates_from = 'model'),
        files_digest = :digest, checks_version = :checks_version,
        extra_list_hash = :extra_list_hash, read_commit = :read_commit,
        read_commit_known = :read_commit_known, path_candidates = NULL,
        requested_paths = NULL
    WHERE org_id = :org_id AND id = :fix_id AND state = 'gathering'
    RETURNING id
    """
)
_STORE_PACK: Final = text(
    """
    INSERT INTO finding_fix_source_packs (fix_id, org_id, files, frames, bytes, expires_at)
    VALUES (:fix_id, :org_id, CAST(:files AS jsonb), CAST(:frames AS jsonb), :bytes,
            :expires_at)
    ON CONFLICT (fix_id) DO UPDATE SET
        files = EXCLUDED.files, frames = EXCLUDED.frames, bytes = EXCLUDED.bytes,
        expires_at = EXCLUDED.expires_at
    WHERE finding_fix_source_packs.org_id = :org_id
    """
)
_ENDED: Final = text(
    """
    UPDATE finding_fixes SET
        state = :state, reason = :reason, state_changed_at = :at, finished_at = :at,
        path_candidates = NULL, requested_paths = NULL
    WHERE org_id = :org_id AND id = :fix_id AND state = 'gathering'
    RETURNING id
    """
)
_DROP_PACK: Final = text(
    "DELETE FROM finding_fix_source_packs WHERE org_id = :org_id AND fix_id = :fix_id"
)
# FR5c's request: the API's row, holding the stack's candidates until answered once.
_BRIEF_REQUEST: Final = text(
    """
    SELECT payload FROM job_results
    WHERE org_id = :org_id AND request_id = CAST(:request_id AS uuid) AND kind = 'brief_paths'
    """
)
_BRIEF_ANSWERED: Final = text(
    """
    UPDATE job_results SET payload = CAST(:payload AS jsonb)
    WHERE org_id = :org_id AND request_id = CAST(:request_id AS uuid) AND kind = 'brief_paths'
      AND payload ? 'candidates'
    RETURNING CAST(payload ->> 'requested_by' AS uuid) AS requested_by
    """
)
_BRIEF_SOURCE: Final = text(
    """
    SELECT finding.project_id, finding.service_name, location.folder,
           location.path_in_production, place.external_id, place.key,
           connection.id AS connection_id, connection.state AS connection_state,
           connection.sealed_credential, connection.github_app_id,
           connection.github_installation_id
    FROM findings AS finding
    JOIN service_code_locations AS location
      ON location.org_id = finding.org_id AND location.service_name = finding.service_name
     AND (location.project_id = finding.project_id OR location.project_id IS NULL)
    JOIN connection_repositories AS place
      ON place.org_id = location.org_id AND place.id = location.connection_repository_id
    JOIN connections AS connection
      ON connection.org_id = place.org_id AND connection.id = place.connection_id
    WHERE finding.org_id = :org_id AND finding.id = :finding_id
    ORDER BY location.project_id IS NULL
    LIMIT 1
    """
)


@dataclass(frozen=True, slots=True)
class _Consent:
    owner: OwnerLists
    require_github_access: bool


@dataclass(slots=True)
class Ending(Exception):  # noqa: N818 — a way out, carried to the one place that writes it
    state: str
    reason: str


@dataclass(slots=True)
class PackReading:
    builder: PackBuilder
    commit: str
    commit_known: bool
    moved: dict[str, bool] = field(default_factory=dict)
    typed_missing: list[str] = field(default_factory=list)
    top_left_out: bool = False
    # FR15b: the asked-for names this round, so a refused one is listed as asked for.
    asked: set[str] = field(default_factory=set)


# ------------------------------------------------------------------ the pack job


async def build_source_pack(
    engine: AsyncEngine,
    *,
    org_id: str,
    fix_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    now: datetime,
) -> str | None:
    """Read and list one suggestion's files. Returns the state it left, or None when it
    found nothing to do."""
    row = await load_fix_row(engine, org_id, fix_id)
    if row is None or row.kind != "source" or row.state != GATHERING:
        return None
    try:
        consent = await _live_consent(engine, row)
        reading = _read(row, consent, await _context(engine, row), keys, endpoints)
    except Ending as ending:
        return await _end(engine, row, ending.state, ending.reason, now=now)
    except GitHubFailure as failure:
        logger.warning(
            "a source read failed", fix_id=fix_id, failure=failure.code, status=failure.status
        )
        if failure.code == "repository_too_large":
            return await _end(engine, row, REFUSED, "repository_too_large", now=now)
        if failure.passing and attempt() < PACK_MAX_ATTEMPTS:
            raise Retry(RETRY_SECONDS, failure.code) from failure
        return await _end(
            engine, row, FAILED, _REASON_OF.get(failure.code, "github_unavailable"), now=now
        )
    if not reading.builder.files:
        return await _end(engine, row, REFUSED, "no_usable_file", now=now)
    return await _store(engine, row, reading, consent, now=now)


async def load_fix_row(engine: AsyncEngine, org_id: str, fix_id: str) -> Any | None:
    async with engine.connect() as connection:
        return (await connection.execute(_LOAD, {"org_id": org_id, "fix_id": fix_id})).one_or_none()


@dataclass(frozen=True, slots=True)
class PackContext:
    path_in_production: str | None
    github_user_id: int | None
    pack_files: list[dict[str, Any]]


async def _context(engine: AsyncEngine, row: Any) -> PackContext:
    ids = {"org_id": row.org_id}
    async with engine.connect() as connection:
        production = await connection.scalar(
            _PRODUCTION_PATH,
            {
                **ids,
                "service_name": row.service_name,
                "repository_id": row.connection_repository_id,
                "folder": row.folder,
                "project_id": row.project_id,
            },
        )
        linked = (
            None
            if row.requested_by is None
            else await connection.scalar(_LINK, {**ids, "user_id": row.requested_by})
        )
        pack = await connection.scalar(_PACK, {**ids, "fix_id": row.id})
    return PackContext(
        path_in_production=production,
        github_user_id=linked,
        pack_files=list(pack or []),
    )


async def _live_consent(engine: AsyncEngine, row: Any) -> _Consent:
    """FR8 at the pack job: a consent, unwithdrawn, covering the row's folder, whose
    provider terms equal the AI connection's now. Otherwise the row ends."""
    if row.connection_repository_id is None:
        raise Ending(FAILED, CONSENT_ENDED_IN_FLIGHT)
    ids = {"org_id": row.org_id, "repository_id": row.connection_repository_id}
    async with engine.connect() as connection:
        current = (await connection.execute(_AI_CONNECTION, {"org_id": row.org_id})).one_or_none()
        consents = (await connection.execute(_CONSENTS, ids)).all()
    live_terms = (
        None
        if current is None or not current.enabled
        else ProviderTerms(
            current.base_url,
            current.wire_format,
            None if current.key_id is None else str(current.key_id),
            current.model,
        )
    )
    for consent in consents:
        agreed = ProviderTerms(
            consent.provider_base_url,
            consent.provider_format,
            None if consent.key_id is None else str(consent.key_id),
            consent.model,
        )
        if terms_match(agreed, live_terms) and folder_covered(consent.folder_scope, row.folder):
            return _Consent(
                owner=OwnerLists(
                    extra_excluded_paths=tuple(consent.extra_excluded_paths),
                    allowed_config_paths=tuple(consent.allowed_config_paths),
                ),
                require_github_access=consent.require_github_access,
            )
    raise Ending(FAILED, CONSENT_ENDED_IN_FLIGHT)


def fix_client(row: Any, keys: ConfiguredKeys, endpoints: FixEndpoints) -> FixGitHub:
    """The Fix App, its key opened in memory. A missing or unreadable App is a person's
    to fix: `permission_missing`."""
    usable = (
        row.connection_kind == "github_fix"
        and row.connection_state != "disconnected"
        and row.sealed_credential is not None
        and row.github_app_id is not None
        and row.github_installation_id is not None
        and row.is_reachable
    )
    if not usable:
        raise Ending(FAILED, "permission_missing")
    try:
        key = box(keys, CONNECTION_CREDENTIAL_PURPOSE).open(
            bytes(row.sealed_credential),
            owner=connection_secret_owner(str(row.org_id), str(row.connection_id)),
        )
    except (SealError, ValueError) as unreadable:
        raise Ending(FAILED, "permission_missing") from unreadable
    return FixGitHub(endpoints, app_id=row.github_app_id, private_key=key)


def _read(
    row: Any,
    consent: _Consent,
    context: PackContext,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
) -> PackReading:
    github = fix_client(row, keys, endpoints)
    token = github.read_token(row.github_installation_id, int(row.external_id))
    try:
        if consent.require_github_access:
            check_access(github, token, row.key, context.github_user_id)
        reader = PackReader(github, token, row.key, row.folder)
        return reader.read(row, consent.owner, context)
    finally:
        github.revoke(token)


def check_access(github: FixGitHub, token: str, key: str, github_user_id: int | None) -> None:
    """D20, before any blob: the person must be linked, and GitHub must say they can read."""
    if github_user_id is None:
        raise Ending(FAILED, "not_linked")
    try:
        login = github.login_of(token, github_user_id)
        permission = github.permission_of(token, key, login)
    except GitHubFailure as failure:
        if failure.passing:
            raise
        raise Ending(FAILED, "access_unknown") from failure
    if permission not in READ_LEVELS:
        raise Ending(FAILED, "no_github_access")


def folder_files(
    github: RepositoryReads, token: str, key: str, folder: str | None, commit: str
) -> list[TreeFile]:
    """The folder's files only, at `commit`, walked down by name, so nothing outside can
    match. Repository paths. The pack job and the check run both read through this."""
    prefix = f"{folder}/" if folder else ""
    sha = github.tree_of_commit(token, key, commit)
    for segment in folder.split("/") if folder else []:
        entries = github.tree(token, key, sha, recursive=False)
        found = [e for e in entries if e.path == segment and e.kind == "tree"]
        if not found:
            return []
        sha = found[0].sha
    entries = github.tree(token, key, sha, recursive=True)
    return [TreeFile(prefix + e.path, e.mode, e.sha, e.size) for e in entries if e.kind == "blob"]


class RepositoryReads(Protocol):
    """The Fix App client's read routes that choosing a pack uses. `FixGitHub` is one;
    the evaluation bed reads its fixture repository from disk through another
    (`tools/ai-eval/fix/eval_pack.py`), so the bed's packs are chosen by this code."""

    def repository(self, token: str, key: str) -> RepositoryFacts: ...

    def head_of(self, token: str, key: str, branch: str) -> str: ...

    def is_ancestor(self, token: str, key: str, commit: str, default_branch: str) -> bool: ...

    def tree_of_commit(self, token: str, key: str, commit: str) -> str: ...

    def tree(self, token: str, key: str, sha: str, *, recursive: bool) -> list[TreeEntry]: ...

    def blob(self, token: str, key: str, sha: str) -> bytes: ...


class PackReader:
    """One repository folder, read at one commit, for one job (and for the bed)."""

    def __init__(self, github: RepositoryReads, token: str, key: str, folder: str | None) -> None:
        self._github = github
        self._token = token
        self._key = key
        self._folder = folder
        self._prefix = f"{folder}/" if folder else ""

    def read(self, row: Any, owner: OwnerLists, context: PackContext) -> PackReading:
        facts = self._github.repository(self._token, self._key)
        head = self._github.head_of(self._token, self._key, facts.default_branch)
        commit, known = head, False
        if row.read_commit and self._github.is_ancestor(
            self._token, self._key, row.read_commit, facts.default_branch
        ):
            commit, known = row.read_commit, True
        tree = self.subtree(commit)
        builder = PackBuilder(tree={file.path: file for file in tree}, owner=owner)
        index = TreeIndex(self._relative(file.path) for file in tree)
        reading = PackReading(builder=builder, commit=commit, commit_known=known)
        if row.candidates_from == FROM_MODEL:
            reading.asked = self._asked(row, context, builder, index)
        else:
            self._first_round(row, context, reading, index)
        if commit != head:
            on_default = {file.path: file.sha for file in self.subtree(head)}
            reading.moved = {
                file.path: on_default.get(file.path) != file.sha for file in builder.files
            }
        return reading

    # ------------------------------------------------------------- the tree

    def subtree(self, commit: str) -> list[TreeFile]:
        return folder_files(self._github, self._token, self._key, self._folder, commit)

    def _relative(self, path: str) -> str:
        return path.removeprefix(self._prefix)

    def _fetch_and_admit(self, builder: PackBuilder, path: str) -> PackFile | None:
        data = self._github.blob(self._token, self._key, builder.tree[path].sha)
        return builder.admit(path, data)

    # ------------------------------------------------------ the first round

    def _first_round(
        self, row: Any, context: PackContext, reading: PackReading, index: TreeIndex
    ) -> None:
        builder = reading.builder
        candidates = [c for c in row.path_candidates or [] if isinstance(c, dict)]
        removed = {
            self._prefix + str(c.get("path")) for c in candidates if c.get("from") == "removed"
        }
        chosen = self._chosen(candidates, context, reading, index)
        top_stack = next((path for path, role, _ in chosen if role == "stack"), None)
        for path, role, why in chosen:
            lines = builder.lines_for(path) if role == "stack" else ()
            if builder.consider(path, role, why, lines):
                self._fetch_and_admit(builder, path)
        reading.top_left_out = top_stack is not None and builder.left_out_named(top_stack)
        self._imports(builder, index, removed)
        for parent in [file for file in list(builder.files) if file.role in ("stack", "named")]:
            test = conventional_test_file(self._relative(parent.path), index)
            tested = None if test is None else self._prefix + test
            if (
                tested is not None
                and tested not in removed
                and builder.consider(tested, "test", {"kind": "test_for", "path": parent.path})
            ):
                self._fetch_and_admit(builder, tested)

    def _chosen(
        self,
        candidates: Sequence[dict[str, Any]],
        context: PackContext,
        reading: PackReading,
        index: TreeIndex,
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """Each candidate as `(repository path, role, why)`, in order, matched to the tree."""
        builder = reading.builder
        chosen: list[tuple[str, Any, dict[str, Any]]] = []
        for candidate in candidates:
            source, raw = candidate.get("from"), candidate.get("path")
            if not isinstance(raw, str) or source == "removed":
                continue
            line = candidate.get("line") if isinstance(candidate.get("line"), int) else None
            if source == "stack":
                match = match_path(raw, index, path_in_production=context.path_in_production)
                if match.ambiguous:
                    builder.ambiguous += 1
                    continue
                if match.path is None:
                    builder.unmatched += 1
                    continue
                path = self._prefix + match.path
                builder.note_frame(path, line)
                chosen.append((path, "stack", {"kind": "stack", "line": line}))
            elif raw in index:
                role = candidate.get("role") if source == "kept" else "named"
                why = candidate.get("why") if source == "kept" else {"kind": "named"}
                chosen.append((self._prefix + raw, role or "named", why or {"kind": "named"}))
            elif source == "person":
                reading.typed_missing.append(raw)
        return chosen

    def _imports(self, builder: PackBuilder, index: TreeIndex, removed: set[str]) -> None:
        """FR12a: the top two stack or named files' imports, one hop, inside the folder."""
        top = [file for file in builder.files if file.role in ("stack", "named")][:IMPORTS_FROM_TOP]
        module = (
            self._go_module(builder, index) if any(f.path.endswith(".go") for f in top) else None
        )
        for parent in top:
            for found in imported_paths(
                self._relative(parent.path), parent.content, index, go_module=module
            ):
                path = self._prefix + found
                if path not in removed and builder.consider(
                    path, "imported", {"kind": "imported_by", "path": parent.path}
                ):
                    self._fetch_and_admit(builder, path)

    def _go_module(self, builder: PackBuilder, index: TreeIndex) -> str | None:
        """The folder's `go.mod`, read to resolve imports and never sent."""
        if "go.mod" not in index:
            return None
        data = self._github.blob(self._token, self._key, builder.tree[self._prefix + "go.mod"].sha)
        return go_module_of(data.decode("utf-8", errors="replace"))

    # ------------------------------------------------- asked-for names (FR15b)

    def _asked(
        self, row: Any, context: PackContext, builder: PackBuilder, index: TreeIndex
    ) -> set[str]:
        """T3.19: the pack as it stands, then each name the model asked for, checked like a
        stack file. A name matching no file is counted, never kept (AC84). The model's
        reason goes on the file's `why` only if it passes FR16's prose checks here too."""
        asked: set[str] = set()
        for stored in context.pack_files:
            path = stored.get("path")
            if isinstance(path, str) and path in builder.tree:
                builder.keep(_kept_file(stored, builder.tree[path], row.files_read))
        refs = {file.ref for file in builder.files}
        for asked_for in (row.requested_paths or [])[:MAX_ASKED_PATHS]:
            name = asked_for.get("path") if isinstance(asked_for, dict) else None
            if not isinstance(name, str) or not _plain_name(name) or name not in index:
                builder.unmatched += 1
                continue
            because = asked_for.get("ref")
            line = asked_for.get("line")
            reason = asked_for.get("reason")
            why = {
                "kind": "asked",
                "ref": because if because in refs else None,
                "line": line if isinstance(line, int) and line > 0 else None,
                "reason": reason
                if isinstance(reason, str) and prose_refusal(reason, None) is None
                else None,
            }
            asked.add(self._prefix + name)
            if builder.consider(self._prefix + name, "asked", why):
                self._fetch_and_admit(builder, self._prefix + name)
        return asked


def _plain_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name) <= MAX_CANDIDATE_CHARACTERS
        and _ASKED_NAME.match(name) is not None
    )


def _kept_file(stored: dict[str, Any], tree_file: TreeFile, files_read: Any) -> PackFile:
    """A pack file from an earlier round, with its role and reason from the listing."""
    listed = {
        each.get("path"): each
        for each in ((files_read or {}).get("files") or [])
        if isinstance(each, dict)
    }
    entry = listed.get(stored["path"], {})
    window = stored.get("window")
    content = str(stored.get("content", ""))
    return PackFile(
        ref=str(stored.get("ref")),
        path=str(stored["path"]),
        sha=str(entry.get("blob_sha", tree_file.sha)),
        mode=str(entry.get("mode", tree_file.mode)),
        role=entry.get("role", "stack"),
        why=dict(entry.get("why") or {}),
        content=content,
        window=None if window is None else (int(window[0]), int(window[1])),
        sent_bytes=int(entry.get("bytes", len(content.encode("utf-8")))),
    )


# ------------------------------------------------------------------ writing it


def files_read_of(reading: PackReading, *, round_number: int, earlier: Any) -> dict[str, Any]:
    """The listing a person reads (design, `files_read`): paths in the tree only, and a
    count of names matching nothing (AC12g)."""
    builder = reading.builder
    earlier_left = [
        each for each in ((earlier or {}).get("left_out") or []) if isinstance(each, dict)
    ]
    return {
        "commit": reading.commit,
        "commit_known": reading.commit_known,
        "round": round_number,
        "files": [
            {
                "ref": file.ref,
                "path": file.path,
                "blob_sha": file.sha,
                "mode": file.mode,
                "role": file.role,
                "why": file.why,
                "moved_on_default": reading.moved.get(file.path, False),
                "lines_sent": None if file.window is None else list(file.window),
                "bytes": file.sent_bytes,
            }
            for file in builder.files
        ],
        "left_out": [
            *earlier_left,
            *(
                {"path": e.path, "reason": e.reason, "bytes": e.bytes}
                | ({"asked": True} if e.path in reading.asked else {})
                for e in builder.left_out
            ),
        ],
        "unmatched": builder.unmatched,
        "ambiguous": builder.ambiguous,
        "typed_missing": reading.typed_missing,
        "top_left_out": reading.top_left_out,
    }


async def _store(
    engine: AsyncEngine, row: Any, reading: PackReading, consent: _Consent, *, now: datetime
) -> str | None:
    builder = reading.builder
    ids = {"org_id": row.org_id, "fix_id": row.id}
    files_read = files_read_of(reading, round_number=row.round, earlier=row.files_read)
    async with engine.begin() as connection:
        moved = (
            await connection.execute(
                _GATHERED,
                {
                    **ids,
                    "at": now,
                    "files_read": json.dumps(files_read),
                    "digest": files_digest([(f.path, f.sha) for f in builder.files]),
                    "checks_version": CHECKS_VERSION,
                    "extra_list_hash": owner_lists_hash(consent.owner),
                    "read_commit": reading.commit,
                    "read_commit_known": reading.commit_known,
                },
            )
        ).one_or_none()
        if moved is None:
            await connection.rollback()
            return None
        await connection.execute(
            _STORE_PACK,
            {
                **ids,
                "files": json.dumps(
                    [
                        {
                            "ref": f.ref,
                            "path": f.path,
                            "content": f.content,
                            "window": None if f.window is None else list(f.window),
                        }
                        for f in builder.files
                    ]
                ),
                "frames": json.dumps(builder.frames()),
                "bytes": builder.sent_bytes,
                "expires_at": now + PACK_LIFETIME,
            },
        )
        await _audit_read(connection, row, purpose=READ_FOR_FIX, files_read=files_read)
    logger.info("a suggestion's files were read", fix_id=str(row.id), files=len(builder.files))
    return "gathered"


async def _audit_read(
    connection: AsyncConnection, row: Any, *, purpose: str, files_read: dict[str, Any]
) -> None:
    """`repository.read`, naming the person who asked and the purpose, with paths and
    their reasons (FR28a, AC82). No login, no content."""
    await connection.execute(
        text(RECORD_CASE_AUDIT_SQL),
        case_audit_parameters(
            org_id=row.org_id,
            actor_user_id=row.requested_by,
            action=REPOSITORY_READ,
            target=f"{FINDING_AUDIT_TARGET}:{row.finding_id}",
            detail={
                "fix_id": str(row.id),
                "purpose": purpose,
                "round": files_read["round"],
                "commit_known": files_read["commit_known"],
                "paths": [
                    {"path": each["path"], "role": each["role"]} for each in files_read["files"]
                ],
                "files": [
                    {"path": each["path"], "reason": each["reason"]}
                    for each in files_read["left_out"]
                ],
            },
        ),
    )


async def _end(
    engine: AsyncEngine, row: Any, state: str, reason: str, *, now: datetime
) -> str | None:
    """A way out: the candidates and the pack go in the same transaction (FR11, AC14)."""
    ids = {"org_id": row.org_id, "fix_id": row.id}
    async with engine.begin() as connection:
        ended = (
            await connection.execute(_ENDED, {**ids, "state": state, "reason": reason, "at": now})
        ).one_or_none()
        if ended is None:
            await connection.rollback()
            return None
        await connection.execute(_DROP_PACK, ids)
    logger.info("a suggestion stopped while reading files", fix_id=str(row.id), reason=reason)
    return state


# ----------------------------------------------------------- the brief's paths


async def match_brief_paths(
    engine: AsyncEngine,
    *,
    org_id: str,
    finding_id: str,
    request_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
) -> list[dict[str, Any]] | None:
    """FR5c: the paths in the tree this finding's stack names, with lines. Tree only, no
    consent, nothing leaves. The API left the stack's candidates in the request's own
    `job_results` row, since a job row carries ids only (AC15); the answer replaces them
    once, so the first result stands. Returns None when there was nothing to answer."""
    ids = {"org_id": org_id, "request_id": request_id}
    async with engine.connect() as connection:
        waiting = await connection.scalar(_BRIEF_REQUEST, ids)
        source = (
            await connection.execute(_BRIEF_SOURCE, {"org_id": org_id, "finding_id": finding_id})
        ).one_or_none()
    if not isinstance(waiting, dict) or "candidates" not in waiting:
        return None
    candidates = [c for c in waiting["candidates"] if isinstance(c, dict)]
    found: list[dict[str, Any]] = []
    if source is not None and source.connection_state != "disconnected":
        try:
            found = _brief_matches(source, org_id, candidates, keys, endpoints)
        except (Ending, GitHubFailure) as failure:
            logger.warning(
                "the brief's paths could not be read", finding_id=finding_id, failure=str(failure)
            )
    async with engine.begin() as connection:
        answered = (
            await connection.execute(
                _BRIEF_ANSWERED, {**ids, "payload": json.dumps({"paths": found})}
            )
        ).one_or_none()
        if answered is not None and found:
            await connection.execute(
                text(RECORD_CASE_AUDIT_SQL),
                case_audit_parameters(
                    org_id=org_id,
                    actor_user_id=answered.requested_by,
                    action=REPOSITORY_READ,
                    target=f"{FINDING_AUDIT_TARGET}:{finding_id}",
                    detail={
                        "purpose": READ_FOR_BRIEF,
                        "paths": [{"path": each["path"], "role": "brief"} for each in found],
                    },
                ),
            )
    return found


def _brief_matches(
    source: Any,
    org_id: str,
    candidates: Sequence[dict[str, Any]],
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
) -> list[dict[str, Any]]:
    row = _BriefRow(source, org_id)
    github = fix_client(row, keys, endpoints)
    token = github.read_token(source.github_installation_id, int(source.external_id))
    try:
        reader = PackReader(github, token, source.key, source.folder)
        facts = github.repository(token, source.key)
        tree = reader.subtree(github.head_of(token, source.key, facts.default_branch))
    finally:
        github.revoke(token)
    prefix = f"{source.folder}/" if source.folder else ""
    tree_paths = {file.path for file in tree}
    index = TreeIndex(path.removeprefix(prefix) for path in tree_paths)
    found: list[dict[str, Any]] = []
    for candidate in candidates:
        raw = candidate.get("path")
        if not isinstance(raw, str):
            continue
        match = match_path(raw, index, path_in_production=source.path_in_production)
        if match.path is None:
            continue
        path = prefix + match.path
        line = candidate.get("line")
        entry = {"path": path, "line": line if isinstance(line, int) else None}
        if entry not in found:
            found.append(entry)
    return found


class _BriefRow:
    """The brief's code location, in the shape `_client` reads."""

    def __init__(self, source: Any, org_id: str) -> None:
        self.connection_kind = "github_fix"
        self.connection_state = source.connection_state
        self.sealed_credential = source.sealed_credential
        self.github_app_id = source.github_app_id
        self.github_installation_id = source.github_installation_id
        self.is_reachable = True
        self.org_id = org_id
        self.connection_id = source.connection_id


# ------------------------------------------------------------- the registered tasks


def _keys() -> ConfiguredKeys:
    return get_settings().connection_secret_keys


def endpoints() -> FixEndpoints:
    return FixEndpoints(private_allowed=get_settings().private_probe_targets_allowed)


@task(BUILD_SOURCE_PACK_TASK)
def build_source_pack_task(*, fix_id: str, org_id: str) -> None:
    with_engine(
        lambda engine: build_source_pack(
            engine,
            org_id=org_id,
            fix_id=fix_id,
            keys=_keys(),
            endpoints=endpoints(),
            now=datetime.now(UTC),
        )
    )


@task(MATCH_BRIEF_PATHS_TASK)
def match_brief_paths_task(*, request_id: str, finding_id: str, org_id: str) -> None:
    with_engine(
        lambda engine: match_brief_paths(
            engine,
            org_id=org_id,
            finding_id=finding_id,
            request_id=request_id,
            keys=_keys(),
            endpoints=endpoints(),
        )
    )
