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

# Rung 2b only, until Step 3's migration adds `source` with its own columns and CHECK.
KIND_GENERAL: Final = "general"
FIX_KINDS: Final = (KIND_GENERAL,)
# Rung 2b's states (spec FR38); Step 3 joins `gathering`, `gathered` and `stale`.
PROPOSING: Final = "proposing"
PROPOSED: Final = "proposed"
REFUSED: Final = "refused"
FAILED: Final = "failed"
FIX_STATES: Final = (PROPOSING, PROPOSED, REFUSED, FAILED)
ENDED_STATES: Final = (PROPOSED, REFUSED, FAILED)

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
FIX_REASONS: Final = GENERAL_REASONS

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
