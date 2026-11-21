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
# Spec, *States*. Step 7 joins `approved` and `opened`.
GATHERING: Final = "gathering"
GATHERED: Final = "gathered"
PROPOSING: Final = "proposing"
PROPOSED: Final = "proposed"
REFUSED: Final = "refused"
FAILED: Final = "failed"
STALE: Final = "stale"
FIX_STATES: Final = (GATHERING, GATHERED, PROPOSING, PROPOSED, REFUSED, FAILED, STALE)
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
FIX_REASONS: Final = tuple(
    dict.fromkeys((*GENERAL_REASONS, *SOURCE_REFUSALS, *SOURCE_FAILURES, *STALE_REASONS))
)

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
CODE_DELETED_REASONS: Final = (
    "ended_short",
    "pull_request_done",
    "aged_out",
    "consent_withdrawn",
    "disconnected",
    "ai_off",
    "access_gone",
    "restored",
)

# The fixed code a second attempt tells the model (D51), never an echo of its words:
# G1's wrong shape and G2's caps.
RETRY_SHAPE: Final = "general_shape"
RETRY_TOO_LONG: Final = "general_too_long"
RETRY_CODES: Final = (RETRY_SHAPE, RETRY_TOO_LONG)

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


# A check run's purpose and outcome (design, `finding_fix_checks`; Step 4 runs it).
FIX_CHECK_PURPOSES: Final = ("patch", "draft", "read")
FIX_CHECK_STATUSES: Final = ("running", "unchanged", "moved", "stale", "refused", "failed")
# Why a consent stopped (design, `fix_source_consents`): an owner, a disconnect, lost
# GitHub access, or a restore.
CONSENT_WITHDRAWN_BY_OWNER: Final = "owner"
CONSENT_WITHDRAWN_REASONS: Final = (
    CONSENT_WITHDRAWN_BY_OWNER,
    "disconnected",
    "access_gone",
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
