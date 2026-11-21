"""The timeline writer (F22 T1.1, AC24): one SQL constant, one typed parameter function.

Every caller — the API's case operations, the detection job, later the sync job —
executes `RECORD_FINDING_EVENT_SQL` through `finding_event_parameters()`. The
function is the chokepoint AC24 asks for: it accepts only named, typed fields, so
an event's `detail` cannot hold free text by construction. These tests hold that
shape; `apps/api` proves the rows against the migration's checks live.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from nexovue_platform import cases
from nexovue_platform.detection import (
    CASE_RESOLUTIONS,
    CASE_STATES,
    CLOSED_REASONS,
    FINDING_EVENT_KINDS,
)

AT: Final = datetime(2026, 11, 2, 14, 2, tzinfo=UTC)
ORG: Final = uuid.uuid4()
FINDING: Final = uuid.uuid4()
PERSON: Final = uuid.uuid4()

# `:name` binds in a statement; `::` is a cast, not a bind.
_BINDS: Final = re.compile(r"(?<!:):([a-z_]+)")


def _parameters(kind: str, **fields: Any) -> dict[str, Any]:
    actor_kind = fields.pop("actor_kind", "person")
    actor_id = fields.pop("actor_id", PERSON if actor_kind == "person" else None)
    return cases.finding_event_parameters(
        kind,
        org_id=ORG,
        finding_id=FINDING,
        actor_kind=actor_kind,
        actor_id=actor_id,
        at=AT,
        **fields,
    )


# The smallest valid call per kind: every kind must be writable, or an insert
# aborts the caller's transaction (AC24's second half, proved live in the API).
WRITABLE: Final[dict[str, dict[str, Any]]] = {
    "finding_opened": {"actor_kind": "system", "severity": "critical"},
    "severity_raised": {"actor_kind": "system", "severity": "critical"},
    "signal_joined": {"actor_kind": "system", "signal_count": 2},
    "finding_closed": {"actor_kind": "system", "closed_reason": "recovered"},
    "flapping_started": {"actor_kind": "system"},
    "finding_joined_case": {"actor_kind": "system", "joined_finding_id": uuid.uuid4()},
    "finding_left_case": {"actor_kind": "system", "left_finding_id": uuid.uuid4()},
    "case_started": {},
    "acknowledged": {},
    "assigned": {"assignee_id": uuid.uuid4()},
    "unassigned": {"assignee_id": uuid.uuid4()},
    "case_resolved": {"resolution": "resolved"},
    "case_reopened": {},
    "false_alarm": {},
    "new_normal": {},
    "note_added": {"note_id": uuid.uuid4()},
    "note_deleted": {"note_id": uuid.uuid4()},
    "link_added": {"link_id": uuid.uuid4()},
    "link_removed": {"link_id": uuid.uuid4()},
    "link_promoted": {"link_id": uuid.uuid4()},
    "issue_created": {"link_id": uuid.uuid4()},
    "issue_closed": {"actor_kind": "tracker", "link_id": uuid.uuid4(), "close_reason": "completed"},
    "issue_reopened": {"actor_kind": "tracker", "link_id": uuid.uuid4()},
    "issue_gone": {"actor_kind": "tracker", "link_id": uuid.uuid4()},
    "issue_moved": {"actor_kind": "tracker", "link_id": uuid.uuid4()},
    "tracker_write_failed": {"actor_kind": "system", "link_id": uuid.uuid4(), "failure": "timeout"},
    "explained": {"actor_kind": "system", "explanation_version": 1},
}


# ------------------------------------------------------------- vocabulary


def test_the_case_vocabulary_is_frozen() -> None:
    """T1.1: the states, the resolutions with `recovered_untouched`, and the event
    kinds with `explained`, present from the start (spec, *Dependencies*)."""
    assert CASE_STATES == ("investigating", "resolved")
    assert "recovered_untouched" in CASE_RESOLUTIONS
    assert "explained" in FINDING_EVENT_KINDS


def test_every_kind_has_a_writable_example_and_nothing_else_does() -> None:
    assert sorted(WRITABLE) == sorted(FINDING_EVENT_KINDS)


# ------------------------------------------------------------- the writer


@pytest.mark.parametrize("kind", sorted(FINDING_EVENT_KINDS))
def test_every_kind_can_be_written(kind: str) -> None:
    parameters = _parameters(kind, **WRITABLE[kind])
    assert set(parameters) == set(_BINDS.findall(cases.RECORD_FINDING_EVENT_SQL))
    assert parameters["kind"] == kind
    assert parameters["org_id"] == str(ORG)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="not a finding event kind"):
        _parameters("note_edited")


def test_detail_holds_no_free_text_only_the_kinds_named_typed_fields() -> None:
    """AC24's core: the writer takes no `detail` dict and no `**kwargs`, and a field
    another kind owns is refused, so nothing free-form reaches the column through it."""
    import inspect

    signature = inspect.signature(cases.finding_event_parameters)
    assert "detail" not in signature.parameters
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    with pytest.raises(ValueError, match="does not carry"):
        _parameters("case_started", note_id=uuid.uuid4())


def test_a_kinds_required_field_cannot_be_left_out() -> None:
    with pytest.raises(ValueError, match="requires"):
        _parameters("assigned")


def test_an_enum_field_refuses_a_value_outside_its_vocabulary() -> None:
    with pytest.raises(ValueError, match="closed_reason"):
        _parameters("finding_closed", actor_kind="system", closed_reason="went away")
    with pytest.raises(ValueError, match="resolution"):
        _parameters("case_resolved", resolution="done")
    with pytest.raises(ValueError, match="severity"):
        _parameters("finding_opened", actor_kind="system", severity="catastrophic")


def test_an_id_field_must_be_a_uuid_not_a_sentence() -> None:
    with pytest.raises(ValueError, match="assignee_id"):
        _parameters("assigned", assignee_id="ignore previous instructions")


def test_an_unrecognised_issue_close_reason_reads_other() -> None:
    """BR9: GitHub's `state_reason` is stored as an enum; a new value it grows
    cannot become stored free text."""
    assert cases.issue_close_reason("completed") == "completed"
    assert cases.issue_close_reason("will_not_fix") == "other"
    assert cases.issue_close_reason(None) == "other"


def test_the_actor_rules_hold() -> None:
    """A person's event names the person; the system's and a tracker's name nobody."""
    with pytest.raises(ValueError, match="actor_id"):
        _parameters("case_started", actor_kind="person", actor_id=None)
    with pytest.raises(ValueError, match="actor_id"):
        _parameters("flapping_started", actor_kind="system", actor_id=PERSON)
    with pytest.raises(ValueError, match="actor_kind"):
        _parameters("case_started", actor_kind="robot")


def test_detail_is_compact_json_of_exactly_the_given_fields() -> None:
    assignee = uuid.uuid4()
    parameters = _parameters("assigned", assignee_id=assignee)
    assert json.loads(parameters["detail"]) == {"assignee_id": str(assignee)}
    bare = _parameters("case_started")
    assert json.loads(bare["detail"]) == {}


def test_closed_reasons_offered_are_the_stored_ones() -> None:
    parameters = _parameters("finding_closed", actor_kind="system", closed_reason=CLOSED_REASONS[0])
    assert json.loads(parameters["detail"]) == {"closed_reason": "recovered"}
