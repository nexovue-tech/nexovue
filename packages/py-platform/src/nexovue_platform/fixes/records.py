"""F5's shared vocabulary for a stored suggestion, its jobs and its limits (design, *Data and
migration impact*, *Jobs*; plan Step 2b).

Here so the API, the AI role, the sweep and the migration's frozen copies name one list.
**No AI code** (`fixes/__init__.py`): the API's rung 1 and 2 path imports nothing from
`nexovue_platform.ai`, and this module is how the general path enqueues without doing so.
Every window, cap and count is a guess, named once.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final, Literal

from nexovue_platform.jobs import JobRequest

# --------------------------------------------------------------- the row

# `source` is rungs 3 to 5, reading the customer's files (Step 3); `general` is rung 2b.
KIND_SOURCE: Final = "source"
KIND_GENERAL: Final = "general"
FIX_KINDS: Final = (KIND_SOURCE, KIND_GENERAL)
# Spec, *States*. Step 7 joins `approved` (a person clicked **Open draft pull request**,
# its job is running) and `opened` (the draft exists on GitHub).
GATHERING: Final = "gathering"
GATHERED: Final = "gathered"
PROPOSING: Final = "proposing"
PROPOSED: Final = "proposed"
REFUSED: Final = "refused"
FAILED: Final = "failed"
STALE: Final = "stale"
APPROVED: Final = "approved"
OPENED: Final = "opened"
# Migration 0079's list, before Step 7: its frozen copy is held to this.
STEP_3_FIX_STATES: Final = (GATHERING, GATHERED, PROPOSING, PROPOSED, REFUSED, FAILED, STALE)
FIX_STATES: Final = (*STEP_3_FIX_STATES, APPROVED, OPENED)
ENDED_STATES: Final = (PROPOSED, REFUSED, FAILED)
# BR8: at most one of these per finding, held by a unique index.
IN_FLIGHT_STATES: Final = (GATHERING, GATHERED, PROPOSING)
# States a reason goes with: a short end, or out of date.
STATES_WITH_A_REASON: Final = (REFUSED, FAILED, STALE)

# FR33's languages, in the order the list shows them.
GeneralLanguage = Literal[
    "python", "javascript", "typescript", "java", "kotlin", "go", "ruby", "php", "csharp", "rust"
]
GENERAL_LANGUAGES: Final[tuple[GeneralLanguage, ...]] = (
    "python",
    "javascript",
    "typescript",
    "java",
    "kotlin",
    "go",
    "ruby",
    "php",
    "csharp",
    "rust",
)

# FR35's abstentions: a good outcome, stored as `refused` with the reason.
GENERAL_ABSTAIN_REASONS: Final = ("evidence_too_thin", "needs_a_decision", "not_a_code_problem")
# G3 to G9 refuse at once, with no retry (FR36).
GENERAL_WITHHELD: Final = tuple(f"general_withheld_G{number}" for number in range(3, 10))
# Why a general row stopped short of a suggestion. Every value has a sentence
# (`sentences.py`, AC8): the job's, the sweep's, and the click's re-checks at run time.
GENERAL_REASONS: Final = (
    *GENERAL_ABSTAIN_REASONS,
    *GENERAL_WITHHELD,
    "ai_off",
    "project_opted_out",
    "consent_ended_in_flight",
    "limit_reached",
    "provider_refused",
    "provider_too_long",
    "provider_unavailable",
    "invalid_output",
    "timed_out",
    "nexovue_error",
)
# Why a source row stopped short, from Step 3's pack job, sweep and click (design, *Every
# sentence*). Step 4 adds the model's and the rules' codes.
SOURCE_REFUSALS: Final = ("no_usable_file", "stack_aged_out", "no_stack", "repository_too_large")
CANCELLED: Final = "cancelled"
LAPSED: Final = "lapsed"
CONSENT_ENDED_IN_FLIGHT: Final = "consent_ended_in_flight"
SOURCE_FAILURES: Final = (
    CANCELLED,
    LAPSED,
    "github_unavailable",
    "permission_missing",
    CONSENT_ENDED_IN_FLIGHT,
    "not_linked",
    "no_github_access",
    "access_unknown",
    "timed_out",
    "nexovue_error",
)
# Out of date (FR21); written by Step 4's check run, named now so the CHECK list is whole.
STALE_REASONS: Final = (
    "rules_changed",
    "mapping_changed",
    "cause_changed",
    "edit_no_longer_applies",
)
# Step 4's codes (design, *Every sentence*): the model's own abstention, a misquote after
# its retry, a rule's refusal, and a stored proposal the rules refuse again (D18's tamper
# signal). Migration 0081 widens the CHECK list with them.
NOT_IN_THESE_FILES: Final = "not_in_these_files"
INVALID_OUTPUT: Final = "invalid_output"
EDIT_DID_NOT_APPLY: Final = "edit_did_not_apply"
CHECKS_REFUSED_AT_CLICK: Final = "checks_refused_at_click"
WITHHELD_RULES: Final = tuple(f"withheld_K{number}" for number in range(1, 11))
SOURCE_ABSTAIN_REASONS: Final = (NOT_IN_THESE_FILES, *GENERAL_ABSTAIN_REASONS)
PROPOSAL_REASONS: Final = (
    NOT_IN_THESE_FILES,
    EDIT_DID_NOT_APPLY,
    *WITHHELD_RULES,
    CHECKS_REFUSED_AT_CLICK,
)
# Step 5's: work on a project that was deleted stops, and its stored code goes (ADR-094).
# Either kind may end this way, so it is in neither kind's own list.
PROJECT_DELETED: Final = "project_deleted"
TAKEN_BACK_REASONS: Final = (PROJECT_DELETED,)
FIX_REASONS: Final = tuple(
    dict.fromkeys(
        (
            *GENERAL_REASONS,
            *SOURCE_REFUSALS,
            *SOURCE_FAILURES,
            *STALE_REASONS,
            *PROPOSAL_REASONS,
            *TAKEN_BACK_REASONS,
        )
    )
)
# Migration 0081's list, before Step 5's code: its frozen copy is held to this.
STEP_4_FIX_REASONS: Final = tuple(r for r in FIX_REASONS if r not in TAKEN_BACK_REASONS)
# Migration 0079's list, before Step 4's codes: its frozen copy is held to this.
STEP_3_FIX_REASONS: Final = tuple(r for r in STEP_4_FIX_REASONS if r not in PROPOSAL_REASONS)

# Where a source row's suggestion starts from (design, `basis`).
BASIS_LIKELY_CAUSE: Final = "likely_cause"
BASIS_STACK_ONLY: Final = "stack_only"
BASIS_NAMED_FILES: Final = "named_files"
BASES: Final = (BASIS_LIKELY_CAUSE, BASIS_STACK_ONLY, BASIS_NAMED_FILES)
# Who named the candidates: the error's stack, a person, or the model asking (FR15b).
FROM_STACK: Final = "stack"
FROM_PERSON: Final = "person"
FROM_MODEL: Final = "model"
CANDIDATE_SOURCES: Final = (FROM_STACK, FROM_PERSON, FROM_MODEL)
# Why a file is in `files_read` (design, `files_read`).
FILE_ROLES: Final = ("stack", "named", "imported", "asked", "test")
# Why stored code was deleted (BR9's table; the restore too).
CODE_ENDED_SHORT: Final = "ended_short"
CODE_PULL_REQUEST_DONE: Final = "pull_request_done"
CODE_AGED_OUT: Final = "aged_out"
CODE_CONSENT_WITHDRAWN: Final = "consent_withdrawn"
CODE_DISCONNECTED: Final = "disconnected"
CODE_AI_OFF: Final = "ai_off"
CODE_ACCESS_GONE: Final = "access_gone"
CODE_RESTORED: Final = "restored"
# Migration 0079's list: its frozen copy is held to this.
STEP_3_CODE_DELETED_REASONS: Final = (
    CODE_ENDED_SHORT,
    CODE_PULL_REQUEST_DONE,
    CODE_AGED_OUT,
    CODE_CONSENT_WITHDRAWN,
    CODE_DISCONNECTED,
    CODE_AI_OFF,
    CODE_ACCESS_GONE,
    CODE_RESTORED,
)
# Step 5 (migration 0082): the project was deleted (ADR-094).
CODE_DELETED_REASONS: Final = (*STEP_3_CODE_DELETED_REASONS, PROJECT_DELETED)

# The fixed code a second attempt tells the model (D51), never an echo of its words:
# G1's wrong shape and G2's caps.
RETRY_SHAPE: Final = "general_shape"
RETRY_TOO_LONG: Final = "general_too_long"
GENERAL_RETRY_CODES: Final = (RETRY_SHAPE, RETRY_TOO_LONG)
# Rung 3's one retry (D51; design, *Why edits and not a diff*): an answer that failed the
# schema, a misquote (K3), or an edit on a never-changed file (K2).
RETRY_SOURCE_SHAPE: Final = "source_shape"
RETRY_SOURCE_MISQUOTE: Final = "source_misquote"
RETRY_SOURCE_NEVER_CHANGED: Final = "source_never_changed"
SOURCE_RETRY_CODES: Final = (
    RETRY_SOURCE_SHAPE,
    RETRY_SOURCE_MISQUOTE,
    RETRY_SOURCE_NEVER_CHANGED,
)
RETRY_CODES: Final = (*GENERAL_RETRY_CODES, *SOURCE_RETRY_CODES)

# The stored answer's cap, checked by the database too (design: 8 KiB).
GENERAL_SUGGESTION_MAX_BYTES: Final = 8 * 1024

# ------------------------------------------------------------- the limits

# A finding's tries in a day (D32): only rows the model answered count, so a provider
# outage cannot lock a finding during an incident.
TRIES_PER_FINDING: Final = 3
TRIES_WINDOW: Final = timedelta(hours=24)
COUNTED_STATES: Final = (PROPOSED, REFUSED)
# F3's `evidence_pending` ends this long after a finding opened (FR9, a guess).
EVIDENCE_PENDING_FOR: Final = timedelta(minutes=15)

# --------------------------------------------------------------- the jobs

# `scheduler-insight`'s third queue, capped so proposals never hold every explanation
# slot (D40, D56: 2 of 8, a guess).
FIX_QUEUE: Final = "insight-fix"
FIX_CONCURRENCY: Final = 2
SUGGEST_GENERAL_FIX_TASK: Final = "nexovue_scheduler.insight.suggest_general_fix"
SWEEP_GENERAL_FIXES_TASK: Final = "nexovue_scheduler.fixes.sweep_general_fixes"
# Two attempts: G1 or G2 gets one more, as a new attempt (D51). A queued job lives 15
# minutes (design, *Jobs*).
SUGGEST_EXPIRES_SECONDS: Final = 15 * 60
SUGGEST_MAX_ATTEMPTS: Final = 2
SUGGEST_SOFT_LIMIT_EXTRA_SECONDS: Final = 20
SUGGEST_HARD_LIMIT_EXTRA_SECONDS: Final = 40
# Audit actions (design, *Audit*): the click, naming the person and the provider host, and
# a rule's refusal, written once by the sweep with the rule alone. On the finding.
FIX_GENERAL_REQUESTED: Final = "fix.general_requested"
FIX_WITHHELD: Final = "fix.withheld"

# What each call's run row says was sent (D69, FR38).
GENERAL_FIELDS_SENT: Final = ("model_input", "language")


def suggest_general_fix_request(
    *, fix_id: Any, org_id: Any, requested_by: Any, call_seconds: int
) -> JobRequest:
    """One general suggestion. Ids only (AC60's rule, FR34): the job reads `model_input` and
    the language from the row itself, and stops when the row has left `proposing`. The
    person goes on the run row, as F3's wording names its clicker."""
    return JobRequest(
        task=SUGGEST_GENERAL_FIX_TASK,
        queue=FIX_QUEUE,
        kwargs={
            "fix_id": str(fix_id),
            "org_id": str(org_id),
            "requested_by": str(requested_by),
        },
        org_id=str(org_id),
        expires_seconds=SUGGEST_EXPIRES_SECONDS,
        max_attempts=SUGGEST_MAX_ATTEMPTS,
        soft_limit_seconds=call_seconds + SUGGEST_SOFT_LIMIT_EXTRA_SECONDS,
        hard_limit_seconds=call_seconds + SUGGEST_HARD_LIMIT_EXTRA_SECONDS,
    )


# A check run's purpose and outcome (design, `finding_fix_checks`): Step 4 runs `read`,
# Step 5 `patch`; `draft` is Step 7's.
CHECK_FOR_READ: Final = "read"
CHECK_FOR_PATCH: Final = "patch"
CHECK_FOR_DRAFT: Final = "draft"
# Who may ask for a suggested fix, as the API's action table says for `REQUEST_FIX`; the
# check run reads it again, so a role removed since the click refuses (design, step 1).
# `test_fix_check_routes.py` holds it equal to the API's table.
ROLES_WHO_ASK: Final = ("owner", "admin", "member")
FIX_CHECK_PURPOSES: Final = (CHECK_FOR_PATCH, CHECK_FOR_DRAFT, CHECK_FOR_READ)
CHECK_RUNNING: Final = "running"
CHECK_UNCHANGED: Final = "unchanged"
CHECK_MOVED: Final = "moved"
CHECK_STALE: Final = "stale"
CHECK_REFUSED: Final = "refused"
CHECK_FAILED: Final = "failed"
FIX_CHECK_STATUSES: Final = (
    CHECK_RUNNING,
    CHECK_UNCHANGED,
    CHECK_MOVED,
    CHECK_STALE,
    CHECK_REFUSED,
    CHECK_FAILED,
)
# A check that passed: nothing moved, or a file moved and every edit still applies (FR21).
CHECK_PASSED: Final = (CHECK_UNCHANGED, CHECK_MOVED)
# Why a check run refused or failed (design, *The check run*, step 3). Migration 0081
# holds the list in a CHECK.
STEP_4_FIX_CHECK_REASONS: Final = (
    "not_linked",
    "no_github_access",
    "access_unknown",
    "github_unavailable",
    "permission_missing",
    "consent_withdrawn",
    "nexovue_error",
)
# Step 5 (migration 0082): a patch check also ends out of date, by the row's own reason, or
# refused by the rules on the same inputs (D18's tamper signal).
STEP_5_FIX_CHECK_REASONS: Final = (
    *STEP_4_FIX_CHECK_REASONS,
    *STALE_REASONS,
    CHECKS_REFUSED_AT_CLICK,
)
# Step 7 (migration 0083): a `draft` check also refuses for the draft's own conditions
# (design, *The check run*, step 3; FR22), and while write access is off in code
# (`write_gate.py`, ADR-097) it refuses before any GitHub call.
WRITES_OFF: Final = "writes_off"
DRAFTS_NOT_ALLOWED: Final = "drafts_not_allowed"
PUBLIC_NOT_ALLOWED: Final = "public_not_allowed"
NO_WRITE_PERMISSION: Final = "no_write_permission"
NO_DRAFT_SUPPORT: Final = "no_draft_support"
NEEDS_LIKELY_CAUSE: Final = "needs_likely_cause"
DRAFT_REFUSALS: Final = (
    WRITES_OFF,
    DRAFTS_NOT_ALLOWED,
    PUBLIC_NOT_ALLOWED,
    NO_WRITE_PERMISSION,
    NO_DRAFT_SUPPORT,
    NEEDS_LIKELY_CAUSE,
)
FIX_CHECK_REASONS: Final = (*STEP_5_FIX_CHECK_REASONS, *DRAFT_REFUSALS)
# D20's guess: a passed `read` check lets this person read quoted code for an hour.
READ_CHECK_LIFETIME: Final = timedelta(hours=1)
# A passed `patch` check is served for this long, from when it finished (design: 15
# minutes, a guess; NFR2).
PATCH_CHECK_LIFETIME: Final = timedelta(minutes=15)
# A click within this long of a finished check returns that one (a guess, design).
CHECK_REUSED_WITHIN: Final = timedelta(seconds=60)
# With no answer in this long, the API reads a check as failed, `check_timed_out` (a guess).
CHECK_ANSWERS_WITHIN: Final = timedelta(minutes=3)
# A running check row lives this long before the sweep deletes it.
CHECK_ROW_LIFETIME: Final = timedelta(minutes=15)
# Why a consent stopped (design, `fix_source_consents`): an owner, a disconnect, lost
# GitHub access, or a restore.
CONSENT_WITHDRAWN_BY_OWNER: Final = "owner"
CONSENT_WITHDRAWN_BY_DISCONNECT: Final = "disconnected"
CONSENT_WITHDRAWN_ACCESS_GONE: Final = "access_gone"
CONSENT_WITHDRAWN_REASONS: Final = (
    CONSENT_WITHDRAWN_BY_OWNER,
    CONSENT_WITHDRAWN_BY_DISCONNECT,
    CONSENT_WITHDRAWN_ACCESS_GONE,
    "restored",
)
# A GitHub account link's attempt (design, `github_link_attempts`).
LINK_WAITING: Final = "waiting"
LINK_RUNNING: Final = "running"
LINK_LINKED: Final = "linked"
LINK_REFUSED: Final = "refused"
LINK_FAILED: Final = "failed"
GITHUB_LINK_STATUSES: Final = (
    LINK_WAITING,
    LINK_RUNNING,
    LINK_LINKED,
    LINK_REFUSED,
    LINK_FAILED,
)
GITHUB_LINK_REASONS: Final = (
    "expired",
    "refused_by_github",
    "github_unavailable",
    "key_unreadable",
)

# ------------------------------------------------- reading files (Step 3)

# FR11, the design's *Picking files*. Every figure is a guess, named once.
MAX_CANDIDATES: Final = 40
MAX_CANDIDATE_CHARACTERS: Final = 200
# D4: a person names at most this many paths.
MAX_TYPED_PATHS: Final = 5
# D10, D48: files sent, bytes sent in all, a whole file's size, and the most a windowed
# file may weigh before it is left out.
MAX_FILES: Final = 12
MAX_SENT_BYTES: Final = 64 * 1024
WHOLE_FILE_MAX_BYTES: Final = 32 * 1024
WINDOWED_FILE_MAX_BYTES: Final = 256 * 1024
# FR13a: the person's hint, "what should it look at?" (D47).
HINT_MAX_CHARACTERS: Final = 500
# The stack's sample, as F3's error group read takes (design, *The flow*, step 1).
STACK_OCCURRENCES: Final = 20
# FR12a, D76: imports of the top two stack files only; a Go import adds at most three files.
IMPORTS_FROM_TOP: Final = 2
GO_FILES_PER_IMPORT: Final = 3
# FR15b, FR15c: the model may ask for at most three names a round, in at most three sends.
MAX_ASKED_PATHS: Final = 3
MAX_ROUNDS: Final = 3
# FR13a: a gathered list waits this long for Send, then lapses, shown neutrally.
GATHERED_LAPSES_AFTER: Final = timedelta(minutes=15)
# The sweep's limit for a row still gathering: the job's 15-minute expiry plus its hard
# limit and a margin (design, *The sweep*, step 1).
GATHERING_STUCK_AFTER: Final = timedelta(minutes=20)

# ------------------------------------------------------- the jobs (Step 3)

BUILD_SOURCE_PACK_TASK: Final = "nexovue_scheduler.connections.build_source_pack"
MATCH_BRIEF_PATHS_TASK: Final = "nexovue_scheduler.connections.match_brief_paths"
COMPLETE_GITHUB_USER_LINK_TASK: Final = "nexovue_scheduler.connections.complete_github_user_link"
CONVERT_FIX_APP_TASK: Final = "nexovue_scheduler.connections.convert_fix_app"
REFRESH_FIX_APP_TASK: Final = "nexovue_scheduler.connections.refresh_fix_app"
SWEEP_FIXES_TASK: Final = "nexovue_scheduler.fixes.sweep_fixes"
# The one name `fix_sweep_status` holds (migration 0085): the end of each whole pass of
# `sweep_fixes`, which beat reads for the operator's alert (T9.1).
FIX_SWEEP_NAME: Final = "sweep_fixes"
# F22's queue for a person's jobs on `scheduler-connections` (design, *Jobs*). A copy,
# because `nexovue_platform.connections` reaches file access this package may not
# (`test_fix_platform_holds_no_ai.py`); `test_fix_source_records.py` holds them equal.
INTERACTIVE_QUEUE: Final = "connections-interactive"
PACK_EXPIRES_SECONDS: Final = 15 * 60
PACK_SOFT_LIMIT_SECONDS: Final = 60
PACK_HARD_LIMIT_SECONDS: Final = 90
# Three attempts, on GitHub's 5xx or rate limit, through `Retry`.
PACK_MAX_ATTEMPTS: Final = 3
LINK_EXPIRES_SECONDS: Final = 5 * 60
LINK_SOFT_LIMIT_SECONDS: Final = 30
LINK_HARD_LIMIT_SECONDS: Final = 60
# A link attempt lives ten minutes (design, `github_link_attempts`); a code is single use.
LINK_ATTEMPT_LIFETIME: Final = timedelta(minutes=10)
# FR5c: the brief's paths are kept an hour for the console's poll, a guess.
BRIEF_PATHS_RESULT_KIND: Final = "brief_paths"
BRIEF_PATHS_TTL_SECONDS: Final = 60 * 60

# Audit actions written by the jobs and the API (design, *Observability*). Details hold
# ids, fixed codes and paths, never a GitHub login or id (AC31).
REPOSITORY_READ: Final = "repository.read"
FIX_REQUESTED: Final = "fix.requested"
FIX_CANCELLED: Final = "fix.cancelled"
GITHUB_ACCOUNT_LINKED: Final = "github_account.linked"
GITHUB_ACCOUNT_UNLINKED: Final = "github_account.unlinked"
# A pack job's purpose, a check run's, and FR5c's.
READ_FOR_FIX: Final = "fix"
READ_FOR_BRIEF: Final = "brief"


def build_source_pack_request(*, fix_id: Any, org_id: Any) -> JobRequest:
    """Read and list a suggestion's files (design, *The flow*, step 3). Ids only (AC15)."""
    return JobRequest(
        task=BUILD_SOURCE_PACK_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"fix_id": str(fix_id), "org_id": str(org_id)},
        org_id=str(org_id),
        expires_seconds=PACK_EXPIRES_SECONDS,
        max_attempts=PACK_MAX_ATTEMPTS,
        soft_limit_seconds=PACK_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=PACK_HARD_LIMIT_SECONDS,
    )


def match_brief_paths_request(*, request_id: Any, finding_id: Any, org_id: Any) -> JobRequest:
    """FR5c: the brief's paths, matched against the tree alone. Ids only."""
    return JobRequest(
        task=MATCH_BRIEF_PATHS_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={
            "request_id": str(request_id),
            "finding_id": str(finding_id),
            "org_id": str(org_id),
        },
        org_id=str(org_id),
        expires_seconds=PACK_EXPIRES_SECONDS,
        max_attempts=PACK_MAX_ATTEMPTS,
        soft_limit_seconds=PACK_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=PACK_HARD_LIMIT_SECONDS,
    )


def complete_github_user_link_request(*, attempt_id: Any, org_id: Any) -> JobRequest:
    """Exchange a person's sealed code once (A18). Ids only: no job row holds a code."""
    return JobRequest(
        task=COMPLETE_GITHUB_USER_LINK_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"attempt_id": str(attempt_id), "org_id": str(org_id)},
        org_id=str(org_id),
        expires_seconds=LINK_EXPIRES_SECONDS,
        max_attempts=1,
        soft_limit_seconds=LINK_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=LINK_HARD_LIMIT_SECONDS,
    )


# --------------------------------------------------- sealed values (Step 3)

# Under `CONNECTION_SECRET_KEY`, each its own purpose (`keys.SEALED_COLUMNS`). The Fix
# App's private key uses F22's credential purpose, bound to its own connection.
FIX_APP_CLIENT_SECRET_PURPOSE: Final = "fix-app-client-secret"
GITHUB_LINK_CODE_PURPOSE: Final = "github-link-code"


def github_link_code_owner(org_id: str, user_id: str, attempt_id: str) -> str:
    """Whom a link attempt's sealed code is bound to: its organization, person and attempt,
    so a code copied onto another row or person will not open."""
    return f"github-link:{org_id}:{user_id}:{attempt_id}"


# ------------------------------------------------------- the suggestion (Step 4)

PROPOSE_FIX_TASK: Final = "nexovue_scheduler.insight.propose_fix"
CHECK_FIX_TASK: Final = "nexovue_scheduler.connections.check_fix_against_current_code"
NOTIFY_REQUESTERS_TASK: Final = "nexovue_scheduler.fixes.notify_requesters"
# Audit actions: each Send, naming the paths, blob ids and provider host (FR13a); a
# person's first read of quoted code (design, `finding_fix_code_views`); a check's result.
FIX_SENT: Final = "fix.sent"
FIX_CODE_VIEWED: Final = "fix.code_viewed"
FIX_CHECK_REFUSED: Final = "fix.check_refused"
# D18's tamper signal: a written suggestion the rules refused again on the same inputs, at
# the sweep's first recording or at a check run (the detail's `at` says which).
FIX_CHECKS_REFUSED: Final = "fix.checks_refused"
# The API's audit words for a consent withdrawn, on its repository, which the sweep writes
# too when GitHub access is gone (`audit/service.py`; held equal by `test_fix_takeback_live.py`).
AI_SOURCE_DISCLOSURE_WITHDRAWN: Final = "ai.source_disclosure_withdrawn"
REPOSITORY_AUDIT_TARGET: Final = "connection_repository"
# Step 5: a patch served from a person's own passed check (design, *Audit*).
FIX_PATCH_DOWNLOADED: Final = "fix.patch_downloaded"
# A person's answer to "Was this suggestion useful?" (FR29, UR11), stored as these codes.
FixVerdict = Literal["merge_as_is", "useful_start", "wrong"]
VERDICT_MERGE_AS_IS: Final = "merge_as_is"
VERDICT_USEFUL_START: Final = "useful_start"
VERDICT_WRONG: Final = "wrong"
FIX_VERDICTS: Final[tuple[FixVerdict, ...]] = ("merge_as_is", "useful_start", "wrong")
# BR9's "GitHub access gone" row: an uninstalled App or an unreachable repository this long
# withdraws consent and deletes stored code (a guess, design *The sweep*, step 6).
ACCESS_GONE_AFTER: Final = timedelta(hours=72)
# How long a backup may still hold deleted code (D29: 7 daily and 4 weekly snapshots,
# `backups.py`), for the "Code deleted" line.
BACKUPS_KEPT_FOR: Final = timedelta(weeks=4)
# What each proposal call's run row says was sent (FR15a, FR38): the files by number, the
# frames by number, the person's hint, and F3's facts when an explanation exists.
SOURCE_FIELDS_SENT: Final = ("model_input", "files", "frames", "hint")
SOURCE_FIELDS_SENT_WITHOUT_FACTS: Final = ("files", "frames", "hint")
# F3's longest call (`ai_records.CALL_SECONDS_MAX`), copied so this package reaches no AI
# module (AC33); `test_fix_sweep_live.py` holds them equal.
CALL_SECONDS_LONGEST: Final = 300
# A pack sent to the AI role outlives every attempt of its job: the queue's life, the call
# and its hard limit's extra, and a margin for the lease (design, *Jobs*).
PROPOSING_MARGIN: Final = timedelta(minutes=2)
CHECK_EXPIRES_SECONDS: Final = 15 * 60
CHECK_SOFT_LIMIT_SECONDS: Final = 60
CHECK_HARD_LIMIT_SECONDS: Final = 90


def propose_fix_request(*, fix_id: Any, org_id: Any, sent_by: Any, call_seconds: int) -> JobRequest:
    """One suggestion from the files sent (design, *The flow*, step 5). Ids only: the job
    reads the pack, the frames and the hint from the store, and stops when the row has left
    `proposing`. Two attempts, the second a new attempt reading `retry_code` (D51)."""
    return JobRequest(
        task=PROPOSE_FIX_TASK,
        queue=FIX_QUEUE,
        kwargs={"fix_id": str(fix_id), "org_id": str(org_id), "sent_by": str(sent_by)},
        org_id=str(org_id),
        expires_seconds=SUGGEST_EXPIRES_SECONDS,
        max_attempts=SUGGEST_MAX_ATTEMPTS,
        soft_limit_seconds=call_seconds + SUGGEST_SOFT_LIMIT_EXTRA_SECONDS,
        hard_limit_seconds=call_seconds + SUGGEST_HARD_LIMIT_EXTRA_SECONDS,
    )


def proposing_lifetime(call_seconds: int) -> timedelta:
    """How long a sent pack, and its `proposing` row, may wait for the AI role."""
    return (
        timedelta(seconds=SUGGEST_EXPIRES_SECONDS + call_seconds + SUGGEST_HARD_LIMIT_EXTRA_SECONDS)
        + PROPOSING_MARGIN
    )


def check_fix_request(*, check_id: Any, org_id: Any) -> JobRequest:
    """A person's check run (design, *The check run*). Ids only."""
    return JobRequest(
        task=CHECK_FIX_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"check_id": str(check_id), "org_id": str(org_id)},
        org_id=str(org_id),
        expires_seconds=CHECK_EXPIRES_SECONDS,
        max_attempts=PACK_MAX_ATTEMPTS,
        soft_limit_seconds=CHECK_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=CHECK_HARD_LIMIT_SECONDS,
    )


# ---------------------------------------------- the draft pull request (Step 7)

# What GitHub says of a draft Nexovue made, followed by F5's own poller (FR27, Step 8);
# `not_opened` when `POST /pulls` was refused or never answered and nothing was found.
PULL_REQUEST_OPEN: Final = "open"
PULL_REQUEST_MERGED: Final = "merged"
PULL_REQUEST_CLOSED: Final = "closed"
PULL_REQUEST_NOT_OPENED: Final = "not_opened"
PULL_REQUEST_UNTRACKED: Final = "untracked"
PULL_REQUEST_STATES: Final = (
    PULL_REQUEST_OPEN,
    PULL_REQUEST_MERGED,
    PULL_REQUEST_CLOSED,
    PULL_REQUEST_NOT_OPENED,
    PULL_REQUEST_UNTRACKED,
)
# Why a draft was not opened, on `last_write_failure` (design, *The draft click*, *Exits*).
# The row goes back to `proposed`; each code has a sentence (`sentences.py`, AC8).
CHANGED_SINCE_CHECK: Final = "changed_since_check"
BRANCH_NOT_OURS: Final = "branch_not_ours"
DID_NOT_FINISH: Final = "did_not_finish"
# Migration 0083's list: its frozen copy is held to this.
STEP_7_WRITE_FAILURES: Final = (
    WRITES_OFF,
    CHANGED_SINCE_CHECK,
    BRANCH_NOT_OURS,
    DID_NOT_FINISH,
    NO_DRAFT_SUPPORT,
    DRAFTS_NOT_ALLOWED,
    PUBLIC_NOT_ALLOWED,
    NO_WRITE_PERMISSION,
    NEEDS_LIKELY_CAUSE,
    "no_github_access",
    "not_linked",
    "access_unknown",
    "permission_missing",
    "github_unavailable",
    "consent_withdrawn",
)
# Step 8 (migration 0084): why Run CI did not move the branch. `branch_changed`: its head is
# neither a commit Nexovue made nor one it built on (FR25); `pull_request_not_open`: the
# poller found the pull request closed or merged before the job ran.
BRANCH_CHANGED: Final = "branch_changed"
PULL_REQUEST_NOT_OPEN: Final = "pull_request_not_open"
WRITE_FAILURES: Final = (*STEP_7_WRITE_FAILURES, BRANCH_CHANGED, PULL_REQUEST_NOT_OPEN)
# The one branch a suggestion may write (FR23): nothing else can be expressed.
FIX_BRANCH_PREFIX: Final = "nexovue/suggestion-"
# A draft click needs this person's own `draft` check, passed within this long (FR22, a guess).
DRAFT_CHECK_WITHIN: Final = timedelta(minutes=15)
# The sweep returns an `approved` row whose job never finished: the job's 15-minute expiry,
# its hard limit and a margin (design, *The sweep*, step 2; a guess).
APPROVED_STUCK_AFTER: Final = timedelta(minutes=20)
# GitHub's refusal of a draft blocks the repository this long, then is ignored (AC27a, a guess).
DRAFT_REFUSAL_HOLDS_FOR: Final = timedelta(hours=24)
# FR24a: the requester's one line, "Why this change".
REQUESTER_NOTE_MAX_CHARACTERS: Final = 200
# The timeline's kind for an opened draft, by ids and counts (FR28a).
FIX_PULL_REQUEST_OPENED_EVENT: Final = "fix_pull_request_opened"
# Audit actions (design, *Observability*): the click, committed with the claim (AC32a); the
# draft opened; a branch deleted; an owner's per-repository choice.
FIX_PULL_REQUEST_ATTEMPTED: Final = "fix.pull_request_attempted"
FIX_PULL_REQUEST_OPENED: Final = "fix.pull_request_opened"
FIX_BRANCH_DELETED: Final = "fix.branch_deleted"
FIX_REPOSITORY_SETTINGS_CHANGED: Final = "fix.repository_settings_changed"
# `repository.read`'s purpose when the draft job reads the files again.
READ_FOR_DRAFT: Final = "draft"

OPEN_FIX_PULL_REQUEST_TASK: Final = "nexovue_scheduler.connections.open_fix_pull_request"
DELETE_FIX_BRANCH_TASK: Final = "nexovue_scheduler.connections.delete_fix_branch"
WRITER_EXPIRES_SECONDS: Final = 15 * 60
WRITER_SOFT_LIMIT_SECONDS: Final = 60
WRITER_HARD_LIMIT_SECONDS: Final = 90
# Three attempts on a passing GitHub failure; adoption makes each safe (AC26).
WRITER_MAX_ATTEMPTS: Final = 3
DELETE_BRANCH_SOFT_LIMIT_SECONDS: Final = 30
DELETE_BRANCH_HARD_LIMIT_SECONDS: Final = 60


def fix_branch(fix_id: Any) -> str:
    """`nexovue/suggestion-<id>`, the only ref a suggestion writes (FR23)."""
    return f"{FIX_BRANCH_PREFIX}{fix_id}"


def open_fix_pull_request_request(*, fix_id: Any, org_id: Any) -> JobRequest:
    """The draft job (design, *The draft click*). Ids only."""
    return JobRequest(
        task=OPEN_FIX_PULL_REQUEST_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"fix_id": str(fix_id), "org_id": str(org_id)},
        org_id=str(org_id),
        expires_seconds=WRITER_EXPIRES_SECONDS,
        max_attempts=WRITER_MAX_ATTEMPTS,
        soft_limit_seconds=WRITER_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=WRITER_HARD_LIMIT_SECONDS,
    )


def delete_fix_branch_request(*, fix_id: Any, org_id: Any, requested_by: Any) -> JobRequest:
    """**Delete this branch** (FR23, AC66): one attempt, ids only."""
    return JobRequest(
        task=DELETE_FIX_BRANCH_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"fix_id": str(fix_id), "org_id": str(org_id), "requested_by": str(requested_by)},
        org_id=str(org_id),
        expires_seconds=WRITER_EXPIRES_SECONDS,
        max_attempts=1,
        soft_limit_seconds=DELETE_BRANCH_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=DELETE_BRANCH_HARD_LIMIT_SECONDS,
    )


# ------------------------------------------- Run CI and following the pull request (Step 8)

RUN_FIX_CI_TASK: Final = "nexovue_scheduler.connections.run_fix_ci"
POLL_FIX_PULL_REQUESTS_TASK: Final = "nexovue_scheduler.connections.poll_fix_pull_requests"
# Run CI's job (design, *Jobs*: 30 s, 60 s, 3 attempts, 15 minutes; guesses).
RUN_CI_SOFT_LIMIT_SECONDS: Final = 30
RUN_CI_HARD_LIMIT_SECONDS: Final = 60
RUN_CI_MAX_ATTEMPTS: Final = 3
# The poller (design, *Jobs*: every 5 minutes, 60 s, 90 s, 1 attempt, 5 minutes; guesses).
POLL_PULL_REQUESTS_EVERY_SECONDS: Final = 5 * 60
POLL_PULL_REQUESTS_EXPIRES_SECONDS: Final = 5 * 60
POLL_PULL_REQUESTS_SOFT_LIMIT_SECONDS: Final = 60
POLL_PULL_REQUESTS_HARD_LIMIT_SECONDS: Final = 90
# A Run CI request whose job never answered (expired, or its worker died past its attempts)
# reads as not finished after the job's expiry, hard limit and a margin, so Run CI is offered
# again (as `APPROVED_STUCK_AFTER` does for a draft; a guess).
CI_REQUEST_STUCK_AFTER: Final = timedelta(minutes=20)
# BR9: code is deleted this long after its pull request was merged or closed (a guess).
PULL_REQUEST_CODE_KEPT_FOR: Final = timedelta(days=7)
# Audit: the Run CI click, committed with the request (design, *Observability*).
FIX_CI_REQUESTED: Final = "fix.ci_requested"


def run_fix_ci_request(*, fix_id: Any, org_id: Any) -> JobRequest:
    """Run CI's job (design, *Run CI*). Ids only: the job reads who asked, and when, from
    the row, so a retry and a second click are told apart by the store."""
    return JobRequest(
        task=RUN_FIX_CI_TASK,
        queue=INTERACTIVE_QUEUE,
        kwargs={"fix_id": str(fix_id), "org_id": str(org_id)},
        org_id=str(org_id),
        expires_seconds=WRITER_EXPIRES_SECONDS,
        max_attempts=RUN_CI_MAX_ATTEMPTS,
        soft_limit_seconds=RUN_CI_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=RUN_CI_HARD_LIMIT_SECONDS,
    )
