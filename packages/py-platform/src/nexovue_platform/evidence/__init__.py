"""F3: the evidence contract every side shares (design, *New pieces*; plan T1.2).

A finding's explanation is a frozen evidence record with code choosing the cause
(design, *Proposed ADR: An explanation is a frozen evidence record*). Detection adds
the gather job by name from here, the scheduler's `evidence` package runs it, and the
API reads what it wrote. **No AI code lives here** (A37): the evidence package must
never import `nexovue_platform.ai`, and this package is what lets it decide wording
without doing so.

Every window, threshold and bound is a guess held in a named constant (spec FR8),
tuned by the bed (plan Step 2c).
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import timedelta
from typing import Any, Final, Literal

from nexovue_platform.jobs import JobRequest

# ------------------------------------------------------------------ the job

EVIDENCE_QUEUE: Final = "evidence"
# At most this many gathers at once in one worker, under the shared floor (NFR2, A56).
EVIDENCE_CONCURRENCY: Final = 2
GATHER_EVIDENCE_TASK: Final = "nexovue_scheduler.evidence.gather"
# The word job's names, here so gather can enqueue it without importing AI code (A37).
INSIGHT_QUEUE: Final = "insight"
WORD_EXPLANATION_TASK: Final = "nexovue_scheduler.insight.word"

# Why a gather runs (design, *Facts: when and how*). `retry_read` re-reads a failed
# read once, 60 seconds later (gather step 9).
OPENED: Final = "opened"
SETTLED: Final = "settled"
CHANGED: Final = "changed"
CLOSED: Final = "closed"
DEPLOY: Final = "deploy"
PERSON: Final = "person"
RETRY_READ: Final = "retry_read"
READ_REASONS: Final = (OPENED, SETTLED, CHANGED, CLOSED, DEPLOY, PERSON, RETRY_READ)
ReadReason = Literal["opened", "settled", "changed", "closed", "deploy", "person", "retry_read"]

# The settled read waits this long after opening: deploy records can arrive late (FR2).
SETTLE_DELAY_SECONDS: Final = 300
RETRY_READ_DELAY_SECONDS: Final = 60
# A gather's job: a held lock retries every 30 seconds, so 8 attempts outlast the
# longest holder (hard limit 90) and its successors (design, gather step 1).
GATHER_EXPIRES_SECONDS: Final = 3600
GATHER_MAX_ATTEMPTS: Final = 8
GATHER_SOFT_LIMIT_SECONDS: Final = 60
GATHER_HARD_LIMIT_SECONDS: Final = 90
LOCK_RETRY_SECONDS: Final = 30
# A person may read again at most once in this long per finding (FR41, a guess).
READ_AGAIN_EVERY: Final = timedelta(minutes=1)
# A signed deploy asks for a read of an open finding on its service that started
# within this long after it (design, enqueue table).
DEPLOY_READ_WINDOW: Final = timedelta(minutes=30)


def gather_evidence_request(
    finding_id: Any, org_id: Any, reason: ReadReason, countdown: float = 0
) -> JobRequest:
    """One gather of one finding's facts. Ids only (AC60)."""
    if reason not in READ_REASONS:
        raise ValueError(f"unknown read reason {reason!r}")
    return JobRequest(
        task=GATHER_EVIDENCE_TASK,
        queue=EVIDENCE_QUEUE,
        kwargs={"finding_id": str(finding_id), "org_id": str(org_id), "reason": reason},
        org_id=str(org_id),
        countdown_seconds=countdown,
        expires_seconds=GATHER_EXPIRES_SECONDS,
        max_attempts=GATHER_MAX_ATTEMPTS,
        soft_limit_seconds=GATHER_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=GATHER_HARD_LIMIT_SECONDS,
    )


def opening_requests(finding_id: Any, org_id: Any) -> tuple[JobRequest, JobRequest]:
    """An opening's two reads: now, and the settled one 5 minutes later (FR2)."""
    return (
        gather_evidence_request(finding_id, org_id, OPENED),
        gather_evidence_request(finding_id, org_id, SETTLED, countdown=SETTLE_DELAY_SECONDS),
    )


# ----------------------------------------------------------------- the items

KINDS: Final = (
    "deploy",
    "error_group",
    "dependency",
    "check",
    "incident",
    "other_finding",
    "traffic",
    "infrastructure",
    "absence",
)
Kind = Literal[
    "deploy",
    "error_group",
    "dependency",
    "check",
    "incident",
    "other_finding",
    "traffic",
    "infrastructure",
    "absence",
]
# What a fact rests on (FR5, A18), strongest first; also FR44's order.
VERIFIED: Final = "verified"
TELEMETRY: Final = "telemetry"
TELEMETRY_TEXT: Final = "telemetry_text"
BASES: Final = (VERIFIED, TELEMETRY, TELEMETRY_TEXT)
Basis = Literal["verified", "telemetry", "telemetry_text"]

# Why an absence is on the record (FR6, FR45, FR51, FR52).
NO_DEPLOY_INFORMATION: Final = "no_deploy_information"
NO_DEPENDENCY_TRACES: Final = "no_dependency_traces"
NO_HOST_NAMES: Final = "no_host_names"
COULD_NOT_READ: Final = "could_not_read"
TRUNCATED: Final = "truncated"
ABSENCE_REASONS: Final = (
    NO_DEPLOY_INFORMATION,
    NO_DEPENDENCY_TRACES,
    NO_HOST_NAMES,
    COULD_NOT_READ,
    TRUNCATED,
)
# Absences a person can fix by setting something up (FR45). A failed read never is.
SETUP_ABSENCES: Final = (NO_DEPLOY_INFORMATION, NO_DEPENDENCY_TRACES)
# What a failed read names (`could_not_read`'s `read`), in the page's words' order.
READS: Final = (
    "deploys",
    "dependencies",
    "traffic",
    "errors",
    "checks",
    "incidents",
    "findings",
    "hosts",
    "intake",
)

LIKELY: Final = "likely"
POSSIBLE: Final = "possible"
STRENGTHS: Final = (LIKELY, POSSIBLE)

# Each rule's stable name (design, *The rules*): F5's reach table keys on it, so it is
# never a position in the list.
RULE_SIGNED_DEPLOY: Final = "signed_deploy"
RULE_DEPENDENCY_FINDING: Final = "dependency_finding"
RULE_SIGNED_DEPLOY_WEAK: Final = "signed_deploy_weak"
RULE_DEPENDENCY_DEPLOY: Final = "dependency_deploy"
RULE_SEEN_VERSION: Final = "seen_version"
RULE_DEPENDENCY_EDGE: Final = "dependency_edge"
RULE_TRAFFIC_ROSE: Final = "traffic_rose"
RULE_NEW_ERROR: Final = "new_error"
RULE_PROJECT_DEPLOY: Final = "project_deploy"
RULE_PROJECT_SHARED_DEPENDENCY: Final = "project_shared_dependency"
# The rules that may name a cause, in FR8's order, then FR11's.
CAUSE_RULES: Final = (
    RULE_SIGNED_DEPLOY,
    RULE_DEPENDENCY_FINDING,
    RULE_SIGNED_DEPLOY_WEAK,
    RULE_DEPENDENCY_DEPLOY,
    RULE_DEPENDENCY_EDGE,
    RULE_TRAFFIC_ROSE,
    RULE_PROJECT_DEPLOY,
    RULE_PROJECT_SHARED_DEPENDENCY,
)
# Rules that only ever support another rule's cause (A75, FR8's rule 8).
SUPPORT_RULES: Final = (RULE_SEEN_VERSION, RULE_NEW_ERROR)

# ------------------------------------------------------- holding the cause (FR50)

# False until the founder's bar is recorded and met (spec, *Founder decisions*, 2; A60).
# Flipped only by a reviewed release, never by a setting.
CAUSE_BAR_RECORDED: Final = False
# The rules released once the bar is met (A85). A rule not here behaves as if the bar
# were unmet: its facts show, and it states no cause, sends no `explained` and starts no
# wording run (AC92).
RELEASED_RULES: Final[frozenset[str]] = frozenset()


# The non-production override's variable (A60), read by the API and the scheduler.
SHOW_HELD_CAUSES_VARIABLE: Final = "SHOW_HELD_CAUSES"


def refuse_held_causes_in_production(*, env: str, show_held_causes: bool) -> None:
    """AC92: production stops rather than show a cause the founder's bar has not met."""
    if env == "production" and show_held_causes:
        raise ValueError(
            f"{SHOW_HELD_CAUSES_VARIABLE} is set; production never shows a cause before "
            "its accuracy bar is met. Unset it."
        )


def cause_released(rule: str | None, *, override: bool) -> bool:
    """Whether a cause from `rule` may be shown, sent or worded. `override` is the
    non-production setting that lets the founder judge Steps 1 and 2 (A60); production
    refuses to start with it set (AC92)."""
    if rule is None:
        return False
    return override or (CAUSE_BAR_RECORDED and rule in RELEASED_RULES)


# ------------------------------------------------------------ the wording slot

WORDING_STATES: Final = ("off", "pending", "done", "failed", "over_limit", "abstained", "held")
# Why wording is `failed` or `off`, a closed list (design, *Wording states*, *Provider
# interface*); the API maps it again on read (A48).
WORDING_REASONS: Final = (
    "died",
    "expired",
    "run_cap",
    "too_long",
    "bad_key",
    "unavailable",
    "limited",
    "refused",
    "invalid",
)

# --------------------------------------------------------------- the bounds

# One finding's facts (FR51, design *Bounds on one finding*; all guesses).
MAX_EDGES_READ: Final = 50
MAX_EVIDENCE_ITEMS: Final = 100
MAX_MODEL_FACTS: Final = 30
MAX_ALIASES: Final = 200
MAX_INPUT_TOKENS: Final = 8000
# Error groups kept per service from one read (design, *Reads*).
MAX_ERROR_GROUPS_PER_SERVICE: Final = 20

# ---------------------------------------------------------------- the names

# A name past this is cut, and shown with an ellipsis (design, *Layout*: about 80
# characters show before a disclosure; the record keeps up to this many).
DISPLAY_NAME_MAX: Final = 200
_ELLIPSIS: Final = "…"
# Bidirectional controls, and the tag block (as `releases.name_allowed`), plus the
# zero-width and invisible format characters a name could hide behind.
_HIDDEN_RANGES: Final = ((0x202A, 0x202E), (0x2066, 0x2069), (0xE0000, 0xE007F))
_HIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})
# A sender-controlled exception type is kept only if it reads as a code identifier
# (A47); otherwise its group id and these words stand for it.
EXCEPTION_TYPE_PATTERN: Final = r"^[A-Za-z_$][\w.$:-]{0,120}$"
UNSHOWABLE_TYPE: Final = "An error type that could not be shown"


def display_name(name: str) -> str:
    """A customer's name as nexovue shows it anywhere: control, bidirectional and
    invisible characters removed, and the length capped. The one helper the API and
    alerts use too (design, gather step 5)."""
    kept = "".join(character for character in name if _shown(character))
    if len(kept) > DISPLAY_NAME_MAX:
        return kept[: DISPLAY_NAME_MAX - 1] + _ELLIPSIS
    return kept


def _shown(character: str) -> bool:
    point = ord(character)
    if any(low <= point <= high for low, high in _HIDDEN_RANGES):
        return False
    return unicodedata.category(character) not in _HIDDEN_CATEGORIES


# --------------------------------------------------------------- segments

# Sentences reach the screen as segments: plain text, a customer's name, or a time the
# client formats in the project's zone (design, *Segments*). A name is never
# nexovue's own words, and nothing is ever built from a value.
Segment = dict[str, str]


def text_segment(value: str) -> Segment:
    return {"text": value}


def name_segment(value: str) -> Segment:
    return {"name": display_name(value)}


def at_segment(iso: str) -> Segment:
    return {"at": iso}


def evidence_id(kind: str, identity: str | uuid.UUID) -> str:
    """A fact's id, from its real identity (FR4): stable across versions, so a rating
    and a citation keep meaning the same fact."""
    return f"{kind}:{identity}"
