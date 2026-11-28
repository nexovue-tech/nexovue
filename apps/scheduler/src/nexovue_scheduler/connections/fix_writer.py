"""The draft job, **Delete this branch** and **Run CI** (F5 design, *The draft click*, the
job's steps 1 to 10, *Run CI*; spec FR22, FR23, FR25, FR27, BR8, BR13, BR14, AC19, AC22,
AC24, AC26, AC26a, AC27a, AC28, AC48, AC51, AC66; plan T7.5, T7.10, T8.1).

**Off in code.** All three jobs ask `write_access_on(go)` first, where the product passes
`write_gate.WRITE_ACCESS_GO` (None until the founder's recorded go, ADR-097; foundation item
33). While it is False they make **no GitHub call of any kind**: an `approved` row goes back
to `proposed` with `writes_off`, **Delete this branch** does nothing, and a Run CI request is
answered `writes_off` on the row. The write routes live on `FixWrites`, whose constructor
refuses without the go, so no write token can be minted either.

With the go, on `scheduler-connections`, through F22's guarded client:

1. The row must be `approved`; withdrawal, a disconnect or a person's AI-off is read again
   before every write below, so no further GitHub write follows one that has committed
   (AC48). A row whose code was deleted only looks up what it may already have made.
2. Two tokens for this one repository: one reads (with `pull_requests: read`), one writes.
3. The person again (still in the organization, write access on GitHub, D6), the draft's
   own conditions (`draft_rules.py`) and the repository's live visibility (BR13).
4. **Adoption first:** a pull request whose head is the stored commit is stored and ends it.
5. A branch whose head is not the stored commit is `branch_not_ours`, nothing written.
6. A stored commit with no branch is reused; GitHub having dropped it clears it.
7. Otherwise the files are re-read and re-checked by the check run's own code; the digest
   must equal what the person approved (`changed_since_check` otherwise). Blobs, one tree on
   the head, one commit carrying `[skip ci]` and `skip-checks: true` unless the owner chose
   CI on open. **The commit is stored before the branch is made.**
8. **The branch is stored before `POST /pulls`,** so the one-draft index holds whatever
   GitHub answers.
9. `POST /pulls` with `draft: true`. A second pull request for the branch is adopted; a
   refused draft is `not_opened`, `no_draft_support`, and never a pull request that is
   not a draft.
10. `opened`, the pull request's facts, `fix.pull_request_opened` and the timeline's event.

A passing GitHub failure is retried while attempts last; then the row goes back to
`proposed` with a fixed code, naming any branch left. Logs carry ids and codes only.

**Run CI** (Step 8), on an `opened` draft whose pull request is open, for the person who
clicked (`ci_requested_by`): the person's write access (D6), the owner's allowance and the
App's grant, and BR13 on GitHub's live answer, as for the draft. The branch's head must be
Nexovue's own: its opening commit, its latest Run CI commit, or the head that commit was
built on, else `branch_changed`. One commit with the head's tree and no skip marker is
**stored, with the request it answers, before the `PATCH`**, which only fast-forwards
`nexovue/suggestion-<id>`. So a retry after the branch moved finishes with no new commit, a
retry before it moves the branch to the stored commit, and a second click makes a second
commit (AC28).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.cases import (
    RECORD_CASE_AUDIT_SQL,
    RECORD_FINDING_EVENT_SQL,
    case_audit_parameters,
    finding_event_parameters,
)
from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.detection import FINDING_AUDIT_TARGET
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes import write_gate
from nexovue_platform.fixes.consent import folder_covered
from nexovue_platform.fixes.draft import ci_done, ci_refusal
from nexovue_platform.fixes.pull_request_text import (
    ChangedFile,
    MarkedLine,
    PullRequestFacts,
    PullRequestText,
    SignalFacts,
    pull_request_text,
    run_ci_commit_message,
)
from nexovue_platform.fixes.recheck import Rechecked
from nexovue_platform.fixes.records import (
    APPROVED,
    BRANCH_CHANGED,
    BRANCH_NOT_OURS,
    CHANGED_SINCE_CHECK,
    DELETE_FIX_BRANCH_TASK,
    FIX_BRANCH_DELETED,
    FIX_PULL_REQUEST_OPENED,
    FIX_PULL_REQUEST_OPENED_EVENT,
    NO_DRAFT_SUPPORT,
    OPEN_FIX_PULL_REQUEST_TASK,
    OPENED,
    PULL_REQUEST_NOT_OPEN,
    PULL_REQUEST_NOT_OPENED,
    PULL_REQUEST_OPEN,
    READ_FOR_DRAFT,
    ROLES_WHO_ASK,
    RUN_CI_MAX_ATTEMPTS,
    RUN_FIX_CI_TASK,
    WRITER_MAX_ATTEMPTS,
    WRITES_OFF,
    fix_branch,
)
from nexovue_platform.fixes.stored import StoredProposal, read_stored
from nexovue_platform.fixes.write_gate import WriteGo, write_access_on
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.connections import draft_rules, fix_patch
from nexovue_scheduler.connections.fix_github import (
    WRITE_LEVELS,
    FixEndpoints,
    FixGitHub,
    FixWrites,
    GitHubFailure,
    PullFacts,
    RepositoryFacts,
    TreeChange,
)
from nexovue_scheduler.connections.source_pack import (
    RETRY_SECONDS,
    Ending,
    check_access,
    endpoints,
    fix_client,
    load_fix_row,
)
from nexovue_scheduler.jobs.registry import Retry, attempt, task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# What a final GitHub failure leaves on the row (design, *Exits*).
_FAILURE_OF: Final = {"permission_missing": "permission_missing"}
_GITHUB_UNAVAILABLE: Final = "github_unavailable"
_CONSENT_WITHDRAWN: Final = "consent_withdrawn"
_ACCESS_UNKNOWN: Final = "access_unknown"
# A pull request may be deleted only once it is closed, or was never opened (FR23, AC66).
_DELETABLE_STATES: Final = frozenset({"closed", PULL_REQUEST_NOT_OPENED})

# Every statement binds the job's own `org_id`, and each move is conditional on `approved`.
_TO_PROPOSED: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'proposed', state_changed_at = :at, last_write_failure = :failure
    WHERE org_id = :org_id AND id = :fix_id AND state = 'approved'
    RETURNING id
    """
)
_STORE_COMMIT: Final = text(
    """
    UPDATE finding_fixes SET pull_request_commit_sha = :sha
    WHERE org_id = :org_id AND id = :fix_id AND state = 'approved'
    RETURNING id
    """
)
_STORE_BRANCH: Final = text(
    """
    UPDATE finding_fixes SET pull_request_branch = :branch
    WHERE org_id = :org_id AND id = :fix_id AND state = 'approved'
    RETURNING id
    """
)
_OPENED: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'opened', state_changed_at = :at, pull_request_state = 'open',
        pull_request_state_at = :at, pull_request_number = :number, pull_request_url = :url,
        pull_request_branch = :branch, last_write_failure = NULL
    WHERE org_id = :org_id AND id = :fix_id AND state = 'approved'
    RETURNING finding_id, approved_by
    """
)
_NOT_OPENED: Final = text(
    """
    UPDATE finding_fixes
    SET state = 'proposed', state_changed_at = :at, pull_request_state = 'not_opened',
        pull_request_state_at = :at, last_write_failure = 'no_draft_support'
    WHERE org_id = :org_id AND id = :fix_id AND state = 'approved'
    RETURNING id
    """
)
_DRAFT_REFUSED_AT: Final = text(
    """
    UPDATE fix_repository_settings SET draft_refused_at = :at
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
    """
)
_STILL_GOING: Final = text(
    """
    SELECT fix.state, fix.code_deleted_at, connection.state AS connection_state
    FROM finding_fixes AS fix
    JOIN connection_repositories AS place
      ON place.org_id = fix.org_id AND place.id = fix.connection_repository_id
    JOIN connections AS connection
      ON connection.org_id = place.org_id AND connection.id = place.connection_id
    WHERE fix.org_id = :org_id AND fix.id = :fix_id
    """
)
_SCOPES: Final = text(
    """
    SELECT folder_scope FROM fix_source_consents
    WHERE org_id = :org_id AND connection_repository_id = :repository_id
      AND withdrawn_at IS NULL
    """
)
_PERSON: Final = text(
    """
    SELECT role::text AS role FROM users
    WHERE org_id = :org_id AND id = :user_id AND removed_at IS NULL
      AND role::text = ANY(CAST(:roles AS text[]))
    """
)
_LINK: Final = text(
    "SELECT github_user_id FROM github_user_links WHERE org_id = :org_id AND user_id = :user_id"
)
_DELETABLE: Final = text(
    """
    SELECT id, org_id, finding_id, pull_request_branch, pull_request_commit_sha,
           ci_commit_sha, pull_request_state
    FROM finding_fixes WHERE org_id = :org_id AND id = :fix_id
    """
)


class _Back(Exception):  # noqa: N818 — a way out, carried to the one place that writes it
    """Back to `proposed` with a fixed code, nothing further written."""

    def __init__(self, failure: str) -> None:
        super().__init__(failure)
        self.failure = failure


class _Halted(Exception):  # noqa: N818
    """A withdrawal or a disconnect committed: no further write, and the row is the
    taking-back's to move (AC48)."""


@dataclass(frozen=True, slots=True)
class _Tokens:
    read: str
    write: str


async def open_pull_request(
    engine: AsyncEngine,
    *,
    org_id: str,
    fix_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    go: WriteGo | None,
    show_held_causes: bool,
    base_url: str,
    now: datetime,
) -> str | None:
    """One run of the draft job. `go` is `write_gate.WRITE_ACCESS_GO` in the product; only
    a test passes another. Returns the state it left, or None when it did nothing."""
    suggestion = await fix_patch.suggestion_of(engine, org_id, fix_id)
    if suggestion is None or suggestion.state != APPROVED:
        return None
    if not write_access_on(go):
        return await _back(engine, suggestion, WRITES_OFF, now=now)
    row = await load_fix_row(engine, org_id, fix_id)
    if row is None:
        return None
    job = _DraftJob(engine, suggestion, row, go=go, now=now)
    try:
        return await job.run(
            keys=keys, endpoints=endpoints, show_held_causes=show_held_causes, base_url=base_url
        )
    except _Halted:
        logger.info("a draft job stopped: the suggestion was taken back", fix_id=fix_id)
        return None
    except _Back as back:
        return await _back(engine, suggestion, back.failure, now=now)
    except Ending as ending:
        return await _back(engine, suggestion, ending.reason, now=now)
    except GitHubFailure as failure:
        logger.warning(
            "a draft job's GitHub call failed",
            fix_id=fix_id,
            failure=failure.code,
            status=failure.status,
        )
        if failure.passing and attempt() < WRITER_MAX_ATTEMPTS:
            raise Retry(RETRY_SECONDS, failure.code) from failure
        return await _back(
            engine, suggestion, _FAILURE_OF.get(failure.code, _GITHUB_UNAVAILABLE), now=now
        )


class _DraftJob:
    """Steps 2 to 10 for one `approved` suggestion."""

    def __init__(
        self, engine: AsyncEngine, suggestion: Any, row: Any, *, go: WriteGo | None, now: datetime
    ) -> None:
        self._engine = engine
        self._suggestion = suggestion
        self._row = row
        self._go = go
        self._now = now
        self._ids = {"org_id": suggestion.org_id, "fix_id": suggestion.id}
        self._fix_id = str(suggestion.id)
        self._commit: str | None = suggestion.pull_request_commit_sha

    async def run(
        self,
        *,
        keys: ConfiguredKeys,
        endpoints: FixEndpoints,
        show_held_causes: bool,
        base_url: str,
    ) -> str | None:
        github = fix_client(self._row, keys, endpoints)
        writes = github.writes(self._go)
        installation, repository = self._row.github_installation_id, int(self._row.external_id)
        read = github.pull_request_read_token(installation, repository)
        try:
            write = writes.token(installation, repository)
        except BaseException:
            github.revoke(read)
            raise
        tokens = _Tokens(read=read, write=write)
        try:
            return await self._steps(
                github, writes, tokens, show_held_causes=show_held_causes, base_url=base_url
            )
        finally:
            github.revoke(tokens.read)
            github.revoke(tokens.write)

    async def _steps(
        self,
        github: FixGitHub,
        writes: FixWrites,
        tokens: _Tokens,
        *,
        show_held_causes: bool,
        base_url: str,
    ) -> str | None:
        key = self._row.key
        if self._suggestion.code_deleted_at is not None:
            return await self._adopt_only(github, tokens)
        await self._still_going()
        login, role = await self._person(github, tokens.read)
        refused = await draft_rules.refusal_before_github(
            self._engine, self._suggestion, show_held_causes=show_held_causes, now=self._now
        )
        if refused is not None:
            raise _Back(refused)
        live = github.repository(tokens.read, key)
        public = await draft_rules.public_refusal(self._engine, self._row, live)
        if public is not None:
            raise _Back(public)
        adopted = self._adoptable(github.pulls_for(tokens.read, key, self._fix_id))
        if adopted is not None:
            return await self._opened(adopted)
        head = github.fix_branch_head(tokens.read, key, self._fix_id)
        if head is not None and head != self._commit:
            raise _Back(BRANCH_NOT_OURS)
        facts = await draft_rules.repository_facts(self._engine, self._suggestion)
        ci_on_open = facts is not None and bool(facts.ci_on_open)
        text_of = await self._text(
            live, login=login, role=role, ci_on_open=ci_on_open, base_url=base_url
        )
        if head is None:
            await self._branch(github, writes, tokens, live, text_of)
        await self._still_going()
        await self._store(_STORE_BRANCH, branch=fix_branch(self._fix_id))
        return await self._open(github, writes, tokens, live, text_of)

    # ------------------------------------------------------------- step 1, 3

    async def _still_going(self) -> None:
        """Read before every write: still `approved` with its code, consent unwithdrawn for
        the folder, the connection not disconnected (AC48)."""
        async with self._engine.connect() as connection:
            found = (await connection.execute(_STILL_GOING, self._ids)).one_or_none()
            scopes = (
                await connection.execute(
                    _SCOPES,
                    {
                        "org_id": self._suggestion.org_id,
                        "repository_id": self._suggestion.connection_repository_id,
                    },
                )
            ).all()
        if (
            found is None
            or found.state != APPROVED
            or found.code_deleted_at is not None
            or found.connection_state == "disconnected"
        ):
            raise _Halted
        if not any(folder_covered(each.folder_scope, self._suggestion.folder) for each in scopes):
            raise _Back(_CONSENT_WITHDRAWN)

    async def _person(self, github: FixGitHub, read: str) -> tuple[str, str]:
        """D6 again: the person who approved is still here, linked, with write access."""
        ids = {"org_id": self._suggestion.org_id, "user_id": self._suggestion.approved_by}
        async with self._engine.connect() as connection:
            role = await connection.scalar(_PERSON, {**ids, "roles": list(ROLES_WHO_ASK)})
            linked = await connection.scalar(_LINK, ids)
        if role is None:
            raise _Back(_ACCESS_UNKNOWN)
        login = check_access(
            github,
            read,
            self._row.key,
            None if linked is None else int(linked),
            levels=WRITE_LEVELS,
        )
        return login, str(role)

    # ---------------------------------------------------------- steps 4 to 6

    def _adoptable(self, pulls: list[PullFacts]) -> PullFacts | None:
        """A pull request on this branch whose head is the commit Nexovue stored."""
        if self._commit is None:
            return None
        return next((each for each in pulls if each.head_sha == self._commit), None)

    async def _adopt_only(self, github: FixGitHub, tokens: _Tokens) -> str | None:
        """Code deleted while approved: read only, store what exists, and end."""
        found = (
            None
            if self._suggestion.pull_request_branch is None
            else self._adoptable(github.pulls_for(tokens.read, self._row.key, self._fix_id))
        )
        if found is not None:
            return await self._opened(found)
        raise _Back(_CONSENT_WITHDRAWN)

    async def _branch(
        self,
        github: FixGitHub,
        writes: FixWrites,
        tokens: _Tokens,
        live: RepositoryFacts,
        text_of: PullRequestText,
    ) -> None:
        """Steps 6 and 7: the stored commit reused, or one built, stored, then branched."""
        if self._commit is not None:
            await self._still_going()
            try:
                writes.create_branch(tokens.write, self._row.key, self._fix_id, self._commit)
                return
            except GitHubFailure as failure:
                if failure.code == "branch_exists":
                    return await self._branch_is_ours(github, tokens)
                if failure.code != "commit_missing":
                    raise
            self._commit = None
            await self._store(_STORE_COMMIT, sha=None)
        commit = await self._build_commit(github, writes, tokens, text_of)
        await self._store(_STORE_COMMIT, sha=commit)
        self._commit = commit
        await self._still_going()
        try:
            writes.create_branch(tokens.write, self._row.key, self._fix_id, commit)
        except GitHubFailure as failure:
            if failure.code != "branch_exists":
                raise
            await self._branch_is_ours(github, tokens)

    async def _branch_is_ours(self, github: FixGitHub, tokens: _Tokens) -> None:
        if github.fix_branch_head(tokens.read, self._row.key, self._fix_id) != self._commit:
            raise _Back(BRANCH_NOT_OURS)

    async def _build_commit(
        self, github: FixGitHub, writes: FixWrites, tokens: _Tokens, text_of: PullRequestText
    ) -> str:
        """Step 7: re-read, re-check, and commit exactly the content the person approved."""
        consent = await fix_patch.covering_consent(self._engine, self._suggestion)
        if consent is None:
            raise _Back(_CONSENT_WITHDRAWN)
        owner, _ = consent
        if await fix_patch.out_of_date(self._engine, self._suggestion, owner) is not None:
            raise _Back(CHANGED_SINCE_CHECK)
        reading = fix_patch.read_changed(github, tokens.read, self._row.key, self._suggestion)
        await fix_patch.audit_read(
            self._engine,
            actor=self._suggestion.approved_by,
            suggestion=self._suggestion,
            paths=reading.paths,
            purpose=READ_FOR_DRAFT,
        )
        outcome = await fix_patch.rechecked(self._engine, self._suggestion, owner, reading)
        if not isinstance(outcome, Rechecked) or outcome.digest != self._suggestion.approved_digest:
            raise _Back(CHANGED_SINCE_CHECK)
        key = self._row.key
        changes = []
        for each in outcome.files:
            await self._still_going()
            blob = writes.create_blob(tokens.write, key, each.content.encode("utf-8"))
            changes.append(TreeChange(each.path, each.mode, blob))
        base_tree = github.tree_of_commit(tokens.read, key, reading.head_sha)
        await self._still_going()
        tree = writes.create_tree(tokens.write, key, base_tree, changes)
        await self._still_going()
        return writes.create_commit(
            tokens.write, key, message=text_of.commit_message, tree=tree, parent=reading.head_sha
        )

    # ---------------------------------------------------------- steps 9, 10

    async def _open(
        self,
        github: FixGitHub,
        writes: FixWrites,
        tokens: _Tokens,
        live: RepositoryFacts,
        text_of: PullRequestText,
    ) -> str | None:
        await self._still_going()
        try:
            pull = writes.open_draft(
                tokens.write,
                self._row.key,
                self._fix_id,
                base=live.default_branch,
                title=text_of.title,
                body=text_of.body,
            )
        except GitHubFailure as failure:
            if failure.code == "draft_refused":
                return await self._not_opened()
            if failure.code != "pull_request_exists":
                raise
            found = self._adoptable(github.pulls_for(tokens.read, self._row.key, self._fix_id))
            if found is None:
                raise _Back(BRANCH_NOT_OURS) from failure
            pull = found
        return await self._opened(pull)

    async def _not_opened(self) -> str | None:
        """AC27a: GitHub refused a draft. Learned for 24 hours; the patch stays offered and
        the leftover branch is named; never a pull request that is not a draft."""
        async with self._engine.begin() as connection:
            moved = (
                await connection.execute(_NOT_OPENED, {**self._ids, "at": self._now})
            ).one_or_none()
            if moved is None:
                return None
            await connection.execute(
                _DRAFT_REFUSED_AT,
                {
                    "org_id": self._suggestion.org_id,
                    "repository_id": self._suggestion.connection_repository_id,
                    "at": self._now,
                },
            )
        logger.info("GitHub refused a draft", fix_id=self._fix_id, failure=NO_DRAFT_SUPPORT)
        return "proposed"

    async def _opened(self, pull: PullFacts) -> str | None:
        async with self._engine.begin() as connection:
            opened = (
                await connection.execute(
                    _OPENED,
                    {
                        **self._ids,
                        "at": self._now,
                        "number": pull.number,
                        "url": pull.url,
                        "branch": fix_branch(self._fix_id),
                    },
                )
            ).one_or_none()
            if opened is None:
                return None
            files = len({each.get("file_ref") for each in self._suggestion.edits or []})
            await connection.execute(
                text(RECORD_CASE_AUDIT_SQL),
                case_audit_parameters(
                    org_id=self._suggestion.org_id,
                    actor_user_id=opened.approved_by,
                    action=FIX_PULL_REQUEST_OPENED,
                    target=f"{FINDING_AUDIT_TARGET}:{opened.finding_id}",
                    detail={
                        "fix_id": self._fix_id,
                        "pull_request_number": pull.number,
                        "branch": fix_branch(self._fix_id),
                    },
                ),
            )
            await connection.execute(
                text(RECORD_FINDING_EVENT_SQL),
                finding_event_parameters(
                    FIX_PULL_REQUEST_OPENED_EVENT,
                    org_id=self._suggestion.org_id,
                    finding_id=opened.finding_id,
                    actor_kind="system",
                    at=self._now,
                    fix_id=self._suggestion.id,
                    step_count=files,
                ),
            )
        logger.info("a draft pull request was opened", fix_id=self._fix_id, number=pull.number)
        return "opened"

    # ------------------------------------------------------------- the text

    async def _text(
        self,
        live: RepositoryFacts,
        *,
        login: str,
        role: str,
        ci_on_open: bool,
        base_url: str,
    ) -> PullRequestText:
        """Every word a template (BR10): files and flags from the stored suggestion as the
        read checks it, the signal from the finding, the login only in a private one."""
        stored = await self._stored()
        roles = {
            str(each.get("path")): str(each.get("role"))
            for each in (self._suggestion.files_read or {}).get("files") or []
            if isinstance(each, dict)
        }
        return pull_request_text(
            PullRequestFacts(
                fix_id=self._fix_id,
                folder=self._suggestion.folder,
                private=live.private,
                ci_on_open=ci_on_open,
                files=[
                    ChangedFile(each.path, roles.get(each.path, ""), each.added, each.removed)
                    for each in stored.files
                ],
                flags=[MarkedLine(flag.kind, flag.path, flag.line) for flag in stored.flags],
                note=self._suggestion.requester_note,
                # FR24a: a repository public by GitHub's live answer gets the note only when
                # the requester confirmed it, even if it was private at the click.
                note_confirmed=bool(self._suggestion.requester_note_confirmed),
                requester_login=login,
                requester_role=role,
                signal=_signal(self._suggestion.signals),
                console_url=f"{base_url.rstrip('/')}/findings/{self._suggestion.finding_id}"
                if base_url
                else None,
            )
        )

    async def _stored(self) -> StoredProposal:
        consent = await fix_patch.covering_consent(self._engine, self._suggestion)
        if consent is None:
            raise _Back(_CONSENT_WITHDRAWN)
        explanation = await fix_patch.explanation_of(self._engine, self._suggestion)
        stored = read_stored(
            self._suggestion.edits,
            self._suggestion.files_read,
            folder=self._suggestion.folder,
            owner=consent[0],
            allowed_evidence=fix_patch.allowed_evidence(explanation, self._suggestion.files_read),
            aliases=None if explanation is None else AliasBook(explanation.aliases),
        )
        if not isinstance(stored, StoredProposal):
            raise _Back(CHANGED_SINCE_CHECK)
        return stored

    async def _store(self, statement: Any, **values: Any) -> None:
        """One write of the job's own progress, in its own transaction (steps 7 and 8)."""
        async with self._engine.begin() as connection:
            stored = (await connection.execute(statement, {**self._ids, **values})).one_or_none()
        if stored is None:
            raise _Halted


def _signal(signals: Any) -> SignalFacts | None:
    """The finding's leading signal and its numbers, as stored by detection."""
    leading = signals[0] if isinstance(signals, list) and signals else None
    if not isinstance(leading, dict) or not isinstance(leading.get("signal"), str):
        return None
    current, normal = leading.get("current"), leading.get("normal")
    if not isinstance(current, int | float) or not isinstance(normal, int | float):
        return None
    return SignalFacts(signal=leading["signal"], current=f"{current:g}", normal=f"{normal:g}")


async def _back(engine: AsyncEngine, suggestion: Any, failure: str, *, now: datetime) -> str | None:
    """Back to `proposed`, with the fixed code, only while still `approved`."""
    async with engine.begin() as connection:
        moved = (
            await connection.execute(
                _TO_PROPOSED,
                {
                    "org_id": suggestion.org_id,
                    "fix_id": suggestion.id,
                    "at": now,
                    "failure": failure,
                },
            )
        ).one_or_none()
    if moved is None:
        return None
    logger.info("a draft was not opened", fix_id=str(suggestion.id), failure=failure)
    return "proposed"


# --------------------------------------------------------- Delete this branch


async def delete_fix_branch(
    engine: AsyncEngine,
    *,
    org_id: str,
    fix_id: str,
    requested_by: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    go: WriteGo | None,
    now: datetime,
) -> str | None:
    """FR23, AC66: only `nexovue/suggestion-<id>`, only when its head is a commit Nexovue
    made, only once its pull request is closed or was never opened, and only for a person
    with write access. Off in code until the go, with no GitHub call."""
    if not write_access_on(go):
        logger.info("a branch was not deleted: write access is off", fix_id=fix_id)
        return None
    async with engine.connect() as connection:
        row = (
            await connection.execute(_DELETABLE, {"org_id": org_id, "fix_id": fix_id})
        ).one_or_none()
        linked = await connection.scalar(_LINK, {"org_id": org_id, "user_id": requested_by})
    if (
        row is None
        or row.pull_request_branch is None
        or row.pull_request_commit_sha is None
        or row.pull_request_state not in _DELETABLE_STATES
    ):
        return None
    place = await load_fix_row(engine, org_id, fix_id)
    if place is None:
        return None
    try:
        return await _delete(engine, row, place, requested_by, linked, keys, endpoints, go, now)
    except (Ending, GitHubFailure) as stopped:
        logger.info("a branch was not deleted", fix_id=fix_id, failure=str(stopped))
        return None


_BRANCH_DELETED: Final = text(
    """
    UPDATE finding_fixes SET pull_request_branch_deleted_at = :at
    WHERE org_id = :org_id AND id = :fix_id
    """
)


async def _delete(
    engine: AsyncEngine,
    row: Any,
    place: Any,
    requested_by: str,
    linked: Any,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    go: WriteGo | None,
    now: datetime,
) -> str | None:
    github = fix_client(place, keys, endpoints)
    writes = github.writes(go)
    installation, repository = place.github_installation_id, int(place.external_id)
    read = github.pull_request_read_token(installation, repository)
    try:
        check_access(
            github, read, place.key, None if linked is None else int(linked), levels=WRITE_LEVELS
        )
        head = github.fix_branch_head(read, place.key, str(row.id))
    finally:
        github.revoke(read)
    if head is None or head not in {row.pull_request_commit_sha, row.ci_commit_sha}:
        logger.info("a branch was not deleted", fix_id=str(row.id), failure=BRANCH_NOT_OURS)
        return None
    write = writes.token(installation, repository)
    try:
        writes.delete_branch(write, place.key, str(row.id))
    finally:
        github.revoke(write)
    async with engine.begin() as connection:
        # Live test A14: the row knows, so the page stops offering a branch that is gone.
        await connection.execute(
            _BRANCH_DELETED, {"org_id": row.org_id, "fix_id": row.id, "at": now}
        )
        await connection.execute(
            text(RECORD_CASE_AUDIT_SQL),
            case_audit_parameters(
                org_id=row.org_id,
                actor_user_id=requested_by,
                action=FIX_BRANCH_DELETED,
                target=f"{FINDING_AUDIT_TARGET}:{row.finding_id}",
                detail={"fix_id": str(row.id), "branch": fix_branch(row.id), "head": head},
            ),
        )
    logger.info("a suggestion's branch was deleted", fix_id=str(row.id), at=now.isoformat())
    return "deleted"


# ------------------------------------------------------------------- Run CI

# Every statement binds the job's own `org_id`; each write is conditional on the draft still
# being `opened` and on the request it answers (`ci_requested_at`), so a newer click wins.
_CI_ROW: Final = text(
    """
    SELECT fix.id, fix.org_id, fix.state, fix.connection_repository_id,
           fix.pull_request_state, fix.pull_request_commit_sha, fix.ci_commit_sha,
           fix.ci_parent_sha, fix.ci_requested_by, fix.ci_requested_at,
           fix.ci_commit_requested_at, fix.ci_pushed_at, fix.last_write_failure
    FROM finding_fixes AS fix
    WHERE fix.org_id = :org_id AND fix.id = :fix_id
    """
)
_CI_STORE_COMMIT: Final = text(
    """
    UPDATE finding_fixes
    SET ci_commit_sha = :sha, ci_parent_sha = :parent, ci_commit_requested_at = :requested_at,
        ci_pushed_at = NULL
    WHERE org_id = :org_id AND id = :fix_id AND state = 'opened'
      AND ci_requested_at = :requested_at
    RETURNING id
    """
)
_CI_PUSHED: Final = text(
    """
    UPDATE finding_fixes SET ci_pushed_at = :at
    WHERE org_id = :org_id AND id = :fix_id AND state = 'opened'
      AND ci_requested_at = :requested_at AND ci_commit_requested_at = :requested_at
    RETURNING id
    """
)
_CI_FAILED: Final = text(
    """
    UPDATE finding_fixes SET last_write_failure = :failure
    WHERE org_id = :org_id AND id = :fix_id AND state = 'opened'
      AND ci_requested_at = :requested_at
    RETURNING id
    """
)
_CI_STILL_GOING: Final = text(
    """
    SELECT fix.state, fix.pull_request_state, fix.ci_requested_at,
           connection.state AS connection_state
    FROM finding_fixes AS fix
    JOIN connection_repositories AS place
      ON place.org_id = fix.org_id AND place.id = fix.connection_repository_id
    JOIN connections AS connection
      ON connection.org_id = place.org_id AND connection.id = place.connection_id
    WHERE fix.org_id = :org_id AND fix.id = :fix_id
    """
)


def _ci_done(row: Any) -> bool:
    return ci_done(
        requested_at=row.ci_requested_at,
        commit_requested_at=row.ci_commit_requested_at,
        pushed_at=row.ci_pushed_at,
    )


async def run_ci(
    engine: AsyncEngine,
    *,
    org_id: str,
    fix_id: str,
    keys: ConfiguredKeys,
    endpoints: FixEndpoints,
    go: WriteGo | None,
    now: datetime,
) -> str | None:
    """One run of Run CI's job. `go` is `write_gate.WRITE_ACCESS_GO` in the product; only a
    test passes another. Returns `requested` once the branch moved for this request, the
    failure it stored, or None when there was nothing to do."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(_CI_ROW, {"org_id": org_id, "fix_id": fix_id})
        ).one_or_none()
    if (
        row is None
        or row.state != OPENED
        or row.ci_requested_at is None
        or row.last_write_failure is not None
        or _ci_done(row)
    ):
        return None
    job = _CiJob(engine, row, go=go, now=now)
    if not write_access_on(go):
        return await job.fail(WRITES_OFF)
    if row.pull_request_state != PULL_REQUEST_OPEN:
        return await job.fail(PULL_REQUEST_NOT_OPEN)
    place = await load_fix_row(engine, org_id, fix_id)
    if place is None:
        return None
    try:
        return await job.run(place, keys=keys, endpoints=endpoints)
    except _Halted:
        logger.info("a Run CI job stopped: the request moved on", fix_id=fix_id)
        return None
    except _Back as back:
        return await job.fail(back.failure)
    except Ending as ending:
        return await job.fail(ending.reason)
    except GitHubFailure as failure:
        logger.warning(
            "a Run CI job's GitHub call failed",
            fix_id=fix_id,
            failure=failure.code,
            status=failure.status,
        )
        if failure.passing and attempt() < RUN_CI_MAX_ATTEMPTS:
            raise Retry(RETRY_SECONDS, failure.code) from failure
        return await job.fail(_FAILURE_OF.get(failure.code, _GITHUB_UNAVAILABLE))


class _CiJob:
    """Run CI for the one request the row held when the job started."""

    def __init__(self, engine: AsyncEngine, row: Any, *, go: WriteGo | None, now: datetime) -> None:
        self._engine = engine
        self._row = row
        self._go = go
        self._now = now
        self._fix_id = str(row.id)
        self._ids = {
            "org_id": row.org_id,
            "fix_id": row.id,
            "requested_at": row.ci_requested_at,
        }

    async def run(self, place: Any, *, keys: ConfiguredKeys, endpoints: FixEndpoints) -> str:
        github = fix_client(place, keys, endpoints)
        writes = github.writes(self._go)
        installation, repository = place.github_installation_id, int(place.external_id)
        read = github.pull_request_read_token(installation, repository)
        try:
            write = writes.token(installation, repository)
        except BaseException:
            github.revoke(read)
            raise
        try:
            return await self._steps(github, writes, _Tokens(read=read, write=write), place.key)
        finally:
            github.revoke(read)
            github.revoke(write)

    async def _steps(self, github: FixGitHub, writes: FixWrites, tokens: _Tokens, key: str) -> str:
        row = self._row
        await self._person(github, tokens.read, key)
        facts = await draft_rules.repository_facts(self._engine, row)
        refused = ci_refusal(
            drafts_allowed=facts is not None and bool(facts.drafts_allowed),
            granted=None if facts is None else facts.granted_permissions,
        )
        if refused is not None:
            raise _Back(refused)
        public = await draft_rules.public_refusal(
            self._engine, row, github.repository(tokens.read, key)
        )
        if public is not None:
            raise _Back(public)
        head = github.fix_branch_head(tokens.read, key, self._fix_id)
        if row.ci_commit_requested_at == row.ci_requested_at and row.ci_commit_sha is not None:
            # A retry: the commit for this request is stored. Moved already, or not yet.
            if head == row.ci_commit_sha:
                return await self._pushed()
            if head is None or head != row.ci_parent_sha:
                raise _Back(BRANCH_CHANGED)
            return await self._move(writes, tokens, key, row.ci_commit_sha)
        ours = {row.pull_request_commit_sha, row.ci_commit_sha, row.ci_parent_sha} - {None}
        if head is None or head not in ours:
            raise _Back(BRANCH_CHANGED)
        tree = github.tree_of_commit(tokens.read, key, head)
        await self._still_going()
        commit = writes.create_commit(
            tokens.write,
            key,
            message=run_ci_commit_message(self._fix_id),
            tree=tree,
            parent=head,
        )
        async with self._engine.begin() as connection:
            stored = (
                await connection.execute(
                    _CI_STORE_COMMIT, {**self._ids, "sha": commit, "parent": head}
                )
            ).one_or_none()
        if stored is None:
            raise _Halted
        return await self._move(writes, tokens, key, commit)

    async def _person(self, github: FixGitHub, read: str, key: str) -> None:
        """D6 again: the person who clicked is still here, linked, with write access."""
        ids = {"org_id": self._row.org_id, "user_id": self._row.ci_requested_by}
        async with self._engine.connect() as connection:
            role = await connection.scalar(_PERSON, {**ids, "roles": list(ROLES_WHO_ASK)})
            linked = await connection.scalar(_LINK, ids)
        if role is None:
            raise _Back(_ACCESS_UNKNOWN)
        check_access(
            github, read, key, None if linked is None else int(linked), levels=WRITE_LEVELS
        )

    async def _still_going(self) -> None:
        """Before each write: still `opened`, open, this request, not disconnected."""
        async with self._engine.connect() as connection:
            found = (
                await connection.execute(
                    _CI_STILL_GOING, {"org_id": self._row.org_id, "fix_id": self._row.id}
                )
            ).one_or_none()
        if (
            found is None
            or found.state != OPENED
            or found.ci_requested_at != self._row.ci_requested_at
            or found.connection_state == "disconnected"
        ):
            raise _Halted
        if found.pull_request_state != PULL_REQUEST_OPEN:
            raise _Back(PULL_REQUEST_NOT_OPEN)

    async def _move(self, writes: FixWrites, tokens: _Tokens, key: str, commit: str) -> str:
        await self._still_going()
        try:
            writes.move_branch(tokens.write, key, self._fix_id, commit)
        except GitHubFailure as failure:
            if failure.code == "not_fast_forward":
                raise _Back(BRANCH_CHANGED) from failure
            raise
        return await self._pushed()

    async def _pushed(self) -> str:
        async with self._engine.begin() as connection:
            await connection.execute(_CI_PUSHED, {**self._ids, "at": self._now})
        logger.info("Run CI moved a suggestion's branch", fix_id=self._fix_id)
        return "requested"

    async def fail(self, failure: str) -> str | None:
        """The request's failure, a fixed code, while the request is still the row's."""
        async with self._engine.begin() as connection:
            stored = (
                await connection.execute(_CI_FAILED, {**self._ids, "failure": failure})
            ).one_or_none()
        if stored is None:
            return None
        logger.info("Run CI did not move the branch", fix_id=self._fix_id, failure=failure)
        return failure


# --------------------------------------------------------------------- tasks


@task(OPEN_FIX_PULL_REQUEST_TASK)
def open_fix_pull_request_task(*, fix_id: str, org_id: str) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: open_pull_request(
            engine,
            org_id=org_id,
            fix_id=fix_id,
            keys=settings.connection_secret_keys,
            endpoints=endpoints(),
            go=write_gate.WRITE_ACCESS_GO,
            show_held_causes=settings.show_held_causes,
            base_url=settings.app_base_url,
            now=datetime.now(UTC),
        )
    )
    logger.info("a draft job ended", fix_id=fix_id, org_id=org_id, did=did)


@task(DELETE_FIX_BRANCH_TASK)
def delete_fix_branch_task(*, fix_id: str, org_id: str, requested_by: str) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: delete_fix_branch(
            engine,
            org_id=org_id,
            fix_id=fix_id,
            requested_by=requested_by,
            keys=settings.connection_secret_keys,
            endpoints=endpoints(),
            go=write_gate.WRITE_ACCESS_GO,
            now=datetime.now(UTC),
        )
    )
    logger.info("a branch deletion job ended", fix_id=fix_id, org_id=org_id, did=did)


@task(RUN_FIX_CI_TASK)
def run_fix_ci_task(*, fix_id: str, org_id: str) -> None:
    settings = get_settings()
    did = with_engine(
        lambda engine: run_ci(
            engine,
            org_id=org_id,
            fix_id=fix_id,
            keys=settings.connection_secret_keys,
            endpoints=endpoints(),
            go=write_gate.WRITE_ACCESS_GO,
            now=datetime.now(UTC),
        )
    )
    logger.info("a Run CI job ended", fix_id=fix_id, org_id=org_id, did=did)
