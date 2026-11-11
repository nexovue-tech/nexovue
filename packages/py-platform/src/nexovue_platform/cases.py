"""Case transitions and the timeline writer (F22, D77): SQL constants with typed
parameter functions, which the API and the scheduler each execute.

Three processes end or reopen a case — the API's operations, the tracker sync and
the sweep — and the scheduler cannot import the API, so the transitions live here
the way `RESOLVE_FINDING_SQL` does. The shared package holds no database driver;
each caller binds these statements on its own connection, and AC103 proves the
callers write identical rows.

**The timeline holds no free text, by construction** (BR9, FR19, AC24).
`finding_event_parameters()` is the one way an event's `detail` is built: it takes
only the named, typed fields its kind carries — enums, UUIDs and numbers — and
refuses everything else. There is no `detail: dict` parameter on purpose.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.alerts.model import SEVERITIES
from nexovue_platform.connections import COMMENT_KINDS, ISSUE_STATES
from nexovue_platform.detection import CASE_RESOLUTIONS, CLOSED_REASONS, FINDING_EVENT_KINDS

# Who did a thing to a case: a person, nexovue itself, or the tracker's side.
EVENT_ACTOR_KINDS: Final = ("person", "system", "tracker")

# GitHub's `state_reason`, stored as an enum so a value it grows later cannot
# become stored free text (BR9, FR16); `issue_close_reason` maps the unknown.
ISSUE_CLOSE_REASONS: Final = ("completed", "not_planned", "duplicate", "other")

# The emails a sweep owes about a case (D90, AC94): one row per email in
# `finding_emails_owed`, deleted before its send.
CASE_EMAIL_KINDS: Final = ("tracker_closed", "tracker_closed_signal_open", "idle_warning")

# FR14's caps, enforced at the edge and by the database alike.
NOTE_MAX_LENGTH: Final = 4000
NOTES_PER_CASE: Final = 500

# FR9, D45: a new finding joins its scope's active case only if a person acted on
# that case this recently, so a forgotten case cannot swallow a new failure. A
# guess. The job's insert and the sweep's healing read the same window.
CASE_JOIN_WINDOW: Final = timedelta(days=7)

# How a case ends by silence (BR13, FR26, FR49), read by the sweep that ends it
# and by the page that says when it will. Each is a guess (D9, D42, D71).
CASE_IDLE_LIMIT: Final = timedelta(days=90)
CASE_IDLE_WARNING_AHEAD: Final = timedelta(days=14)
CASE_UNTOUCHED_AFTER: Final = timedelta(hours=72)

# A tracker failure as an enum spelling, never a response's words.
_FAILURE_PATTERN_LENGTH: Final = 64


def issue_close_reason(value: str | None) -> str:
    """A tracker's close reason as the stored enum; anything unrecognised is `other`."""
    return value if value in ISSUE_CLOSE_REASONS else "other"


# ---------------------------------------------------------------- the timeline

RECORD_FINDING_EVENT_SQL: Final = """
INSERT INTO finding_events (org_id, finding_id, kind, actor_kind, actor_id, detail, at)
VALUES (:org_id, :finding_id, :kind, :actor_kind, :actor_id, CAST(:detail AS jsonb), :at)
RETURNING id
"""


def _uuid(name: str, value: Any) -> str:
    """An id field is an id: a UUID, or a string that parses as one."""
    if isinstance(value, uuid.UUID):
        return str(value)
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as wrong:
        raise ValueError(f"{name} must be a UUID, got {value!r}") from wrong


def _enum(vocabulary: tuple[str, ...]) -> Callable[[str, Any], str]:
    def check(name: str, value: Any) -> str:
        if value not in vocabulary:
            raise ValueError(f"{name} must be one of {vocabulary}, got {value!r}")
        return str(value)

    return check


def _count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _failure(name: str, value: Any) -> str:
    """A failure enum spelling: short, lowercase words, never a response's text."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _FAILURE_PATTERN_LENGTH
        or not all(part.isalpha() and part.islower() for part in value.split("_"))
    ):
        raise ValueError(f"{name} must be a short lowercase enum spelling, got {value!r}")
    return value


# Each detail field's validator: the closed set of what an event may carry.
_FIELD_CHECKS: Final[dict[str, Callable[[str, Any], Any]]] = {
    "severity": _enum(SEVERITIES),
    "signal_count": _count,
    "closed_reason": _enum(CLOSED_REASONS),
    "joined_finding_id": _uuid,
    "left_finding_id": _uuid,
    "assignee_id": _uuid,
    "resolution": _enum(CASE_RESOLUTIONS),
    "note_id": _uuid,
    "link_id": _uuid,
    "close_reason": _enum(ISSUE_CLOSE_REASONS),
    "failure": _failure,
    "explanation_version": _count,
}

# What each kind carries — all of it, required. A kind outside this table does
# not exist, and a field outside its kind's row is refused.
_KIND_FIELDS: Final[dict[str, frozenset[str]]] = {
    "finding_opened": frozenset({"severity"}),
    "severity_raised": frozenset({"severity"}),
    "signal_joined": frozenset({"signal_count"}),
    "finding_closed": frozenset({"closed_reason"}),
    "flapping_started": frozenset(),
    "finding_joined_case": frozenset({"joined_finding_id"}),
    "finding_left_case": frozenset({"left_finding_id"}),
    "case_started": frozenset(),
    "acknowledged": frozenset(),
    "assigned": frozenset({"assignee_id"}),
    "unassigned": frozenset({"assignee_id"}),
    "case_resolved": frozenset({"resolution"}),
    "case_reopened": frozenset(),
    "false_alarm": frozenset(),
    "new_normal": frozenset(),
    "note_added": frozenset({"note_id"}),
    "note_deleted": frozenset({"note_id"}),
    "link_added": frozenset({"link_id"}),
    "link_removed": frozenset({"link_id"}),
    "link_promoted": frozenset({"link_id"}),
    "issue_created": frozenset({"link_id"}),
    "issue_closed": frozenset({"link_id", "close_reason"}),
    "issue_reopened": frozenset({"link_id"}),
    "issue_gone": frozenset({"link_id"}),
    "issue_moved": frozenset({"link_id"}),
    "tracker_write_failed": frozenset({"link_id", "failure"}),
    "explained": frozenset({"explanation_version"}),
}

assert sorted(_KIND_FIELDS) == sorted(FINDING_EVENT_KINDS), (
    "every finding event kind needs its detail fields declared here"
)


def finding_event_parameters(
    kind: str,
    *,
    org_id: Any,
    finding_id: Any,
    actor_kind: str,
    at: datetime,
    actor_id: Any = None,
    severity: Any = None,
    signal_count: Any = None,
    closed_reason: Any = None,
    joined_finding_id: Any = None,
    left_finding_id: Any = None,
    assignee_id: Any = None,
    resolution: Any = None,
    note_id: Any = None,
    link_id: Any = None,
    close_reason: Any = None,
    failure: Any = None,
    explanation_version: Any = None,
) -> dict[str, Any]:
    """`RECORD_FINDING_EVENT_SQL`'s bound values, from named, typed fields alone.

    A person's event names its actor; the system's and a tracker's name nobody.
    The kind's fields are all required and no other is accepted, so a caller
    cannot slip a sentence, a title or a pasted address into the timeline.
    """
    if kind not in _KIND_FIELDS:
        raise ValueError(f"{kind!r} is not a finding event kind")
    if actor_kind not in EVENT_ACTOR_KINDS:
        raise ValueError(f"actor_kind must be one of {EVENT_ACTOR_KINDS}, got {actor_kind!r}")
    if (actor_id is None) == (actor_kind == "person"):
        raise ValueError(
            "actor_id is required for a person's event and refused for the system's or a tracker's"
        )

    given = {
        name: value
        for name, value in {
            "severity": severity,
            "signal_count": signal_count,
            "closed_reason": closed_reason,
            "joined_finding_id": joined_finding_id,
            "left_finding_id": left_finding_id,
            "assignee_id": assignee_id,
            "resolution": resolution,
            "note_id": note_id,
            "link_id": link_id,
            "close_reason": close_reason,
            "failure": failure,
            "explanation_version": explanation_version,
        }.items()
        if value is not None
    }
    allowed = _KIND_FIELDS[kind]
    stray = sorted(set(given) - allowed)
    if stray:
        raise ValueError(f"{kind} does not carry {stray}")
    missing = sorted(allowed - set(given))
    if missing:
        raise ValueError(f"{kind} requires {missing}")

    detail = {name: _FIELD_CHECKS[name](name, value) for name, value in given.items()}
    return {
        "org_id": str(org_id),
        "finding_id": str(finding_id),
        "kind": kind,
        "actor_kind": actor_kind,
        "actor_id": None if actor_id is None else _uuid("actor_id", actor_id),
        "detail": json.dumps(detail),
        "at": at,
    }


# ------------------------------------------------------------ the transitions

# Only an investigating case resolves, so a second run — a race between a person,
# the sync and the sweep — matches no row instead of rewriting how it ended. The
# warning marker clears with it, so a reopened case can warn afresh.
RESOLVE_CASE_SQL: Final = """
UPDATE findings
SET case_state = 'resolved', case_resolution = :resolution, case_resolved_at = :at,
    case_resolved_by = :resolved_by, case_warned_at = NULL, updated_at = now()
WHERE org_id = :org_id AND id = :finding_id AND case_state = 'investigating'
RETURNING id
"""


def resolve_case_parameters(
    *, org_id: Any, finding_id: Any, resolution: str, at: datetime, resolved_by: Any
) -> dict[str, Any]:
    """`resolved_by` is the person, or None for the system or a tracker."""
    if resolution not in CASE_RESOLUTIONS:
        raise ValueError(f"resolution must be one of {CASE_RESOLUTIONS}, got {resolution!r}")
    return {
        "org_id": str(org_id),
        "finding_id": str(finding_id),
        "resolution": resolution,
        "at": at,
        "resolved_by": None if resolved_by is None else _uuid("resolved_by", resolved_by),
    }


# Entering `investigating` by any route sets `case_activity_at` (D74), so a
# reopen is not undone by the next sweep while the issue stays open.
REOPEN_CASE_SQL: Final = """
UPDATE findings
SET case_state = 'investigating', case_resolution = NULL, case_resolved_at = NULL,
    case_resolved_by = NULL, case_activity_at = :at, case_warned_at = NULL,
    updated_at = now()
WHERE org_id = :org_id AND id = :finding_id AND case_state = 'resolved'
RETURNING id
"""


def reopen_case_parameters(*, org_id: Any, finding_id: Any, at: datetime) -> dict[str, Any]:
    return {"org_id": str(org_id), "finding_id": str(finding_id), "at": at}


# However a case resolves, an open joined finding leaves it in that transaction
# (FR6, D44) and becomes a plain finding that can start its own case. Closed
# joined findings stay attached, for the timeline.
DETACH_OPEN_JOINED_SQL: Final = """
UPDATE findings
SET root_finding_id = NULL, updated_at = now()
WHERE org_id = :org_id AND root_finding_id = :root_finding_id AND state = 'open'
RETURNING id
"""


def detach_open_joined_parameters(*, org_id: Any, root_finding_id: Any) -> dict[str, Any]:
    return {"org_id": str(org_id), "root_finding_id": str(root_finding_id)}


# Bring the scope's open finding into a case (FR11, D44): on starting a case on a
# closed finding, and on reopen. It never captures a finding that holds a case of
# its own, or already joined one — BR7's 409 is decided by the caller.
JOIN_OPEN_SQL: Final = """
UPDATE findings
SET root_finding_id = :root_finding_id, updated_at = now()
WHERE org_id = :org_id AND project_id = :project_id AND scope = :scope
  AND service_name IS NOT DISTINCT FROM :service_name AND state = 'open'
  AND id <> :root_finding_id AND case_state IS NULL AND root_finding_id IS NULL
RETURNING id
"""


def join_open_parameters(
    *, org_id: Any, project_id: Any, scope: str, service_name: str | None, root_finding_id: Any
) -> dict[str, Any]:
    return {
        "org_id": str(org_id),
        "project_id": str(project_id),
        "scope": scope,
        "service_name": service_name,
        "root_finding_id": str(root_finding_id),
    }


# What the synced issue should become, and the one comment owed with it (BR10,
# D72). Shared so the API, the sync and the sweep cannot drift on its shape. When
# the act wants what the tracker was last seen to say, nothing is pending: the
# wanted state goes back to null, and so does a comment owed with no change to make.
SET_WANTED_STATE_SQL: Final = """
UPDATE finding_links
SET wanted_state = CASE WHEN seen_state IS NOT DISTINCT FROM :wanted_state THEN NULL
                        ELSE :wanted_state END,
    comment_wanted = CASE WHEN seen_state IS NOT DISTINCT FROM :wanted_state THEN NULL
                          ELSE :comment_wanted END,
    next_sync_at = :at
WHERE org_id = :org_id AND finding_id = :finding_id
  AND role = 'synced' AND sync_status NOT IN ('stopped', 'refused')
RETURNING id
"""


def set_wanted_state_parameters(
    *,
    org_id: Any,
    finding_id: Any,
    wanted_state: str,
    comment_wanted: str | None,
    at: datetime,
) -> dict[str, Any]:
    if wanted_state not in ISSUE_STATES:
        raise ValueError(f"wanted_state must be one of {ISSUE_STATES}, got {wanted_state!r}")
    if comment_wanted is not None and comment_wanted not in COMMENT_KINDS:
        raise ValueError(f"comment_wanted must be one of {COMMENT_KINDS}, got {comment_wanted!r}")
    return {
        "org_id": str(org_id),
        "finding_id": str(finding_id),
        "wanted_state": wanted_state,
        "comment_wanted": comment_wanted,
        "at": at,
    }


# Whatever resolves a case ends a hold (D73): the synced issue's "closed in the tracker
# while the signal was open" mark goes in that transaction, so the page stops saying so.
CLEAR_HOLD_SQL: Final = """
UPDATE finding_links
SET tracker_closed_at = NULL
WHERE org_id = :org_id AND finding_id = :finding_id
  AND role = 'synced' AND sync_status <> 'stopped' AND tracker_closed_at IS NOT NULL
RETURNING id
"""


def clear_hold_parameters(*, org_id: Any, finding_id: Any) -> dict[str, Any]:
    return {"org_id": str(org_id), "finding_id": str(finding_id)}


# One owed case email (D90, AC94): a row per email, so a second owed before the
# sweep sends cannot overwrite the first. It holds no name, address or text; the
# sweep deletes it before sending, so a crash loses an email rather than repeating
# one. The sweep's idle warning and the tracker sync both owe them.
RECORD_EMAIL_OWED_SQL: Final = """
INSERT INTO finding_emails_owed (org_id, finding_id, kind, at)
VALUES (:org_id, :finding_id, :kind, :at)
"""


def email_owed_parameters(
    *, org_id: Any, finding_id: Any, kind: str, at: datetime
) -> dict[str, Any]:
    if kind not in CASE_EMAIL_KINDS:
        raise ValueError(f"kind must be one of {CASE_EMAIL_KINDS}, got {kind!r}")
    return {"org_id": str(org_id), "finding_id": str(finding_id), "kind": kind, "at": at}


# The audit row every case writer adds in its own transaction (FD9, AC77). The
# scheduler's endings name no person, as `project_job`'s adoption does.
RECORD_CASE_AUDIT_SQL: Final = """
INSERT INTO audit_log (org_id, actor_user_id, action, target, detail)
VALUES (:org_id, :actor_user_id, :action, :target, CAST(:detail AS jsonb))
"""


def case_audit_parameters(
    *,
    org_id: Any,
    actor_user_id: Any,
    action: str,
    target: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "org_id": str(org_id),
        "actor_user_id": None if actor_user_id is None else _uuid("actor_user_id", actor_user_id),
        "action": action,
        "target": target,
        "detail": json.dumps(dict(detail or {})),
    }
