"""The shared case transitions (F22 T1.4, D77): SQL constants the API and the
scheduler both execute, with parameter functions that spell their binds once.

AC103 later proves the three callers write identical rows live; this holds the
offline half — each statement binds the tenant, each parameter function matches
its statement's binds exactly, and the rules the design fixes are in the SQL
itself: entering `investigating` sets `case_activity_at` (D74), and a resolve
moves only an investigating case, so running it twice changes nothing.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from nexovue_platform import cases

AT: Final = datetime(2026, 11, 2, 16, 10, tzinfo=UTC)
ORG: Final = uuid.uuid4()

_BINDS: Final = re.compile(r"(?<!:):([a-z_]+)")

STATEMENTS: Final[dict[str, tuple[str, dict[str, Any]]]] = {
    "resolve": (
        cases.RESOLVE_CASE_SQL,
        cases.resolve_case_parameters(
            org_id=ORG, finding_id=uuid.uuid4(), resolution="resolved", at=AT, resolved_by=None
        ),
    ),
    "reopen": (
        cases.REOPEN_CASE_SQL,
        cases.reopen_case_parameters(org_id=ORG, finding_id=uuid.uuid4(), at=AT),
    ),
    "detach": (
        cases.DETACH_OPEN_JOINED_SQL,
        cases.detach_open_joined_parameters(org_id=ORG, root_finding_id=uuid.uuid4()),
    ),
    "join": (
        cases.JOIN_OPEN_SQL,
        cases.join_open_parameters(
            org_id=ORG,
            project_id=uuid.uuid4(),
            scope="service",
            service_name="checkout",
            root_finding_id=uuid.uuid4(),
        ),
    ),
    "wanted_state": (
        cases.SET_WANTED_STATE_SQL,
        cases.set_wanted_state_parameters(
            org_id=ORG,
            finding_id=uuid.uuid4(),
            wanted_state="closed",
            comment_wanted="resolved",
            at=AT,
        ),
    ),
    "email_owed": (
        cases.RECORD_EMAIL_OWED_SQL,
        cases.email_owed_parameters(
            org_id=ORG, finding_id=uuid.uuid4(), kind="idle_warning", at=AT
        ),
    ),
    "audit": (
        cases.RECORD_CASE_AUDIT_SQL,
        cases.case_audit_parameters(
            org_id=ORG,
            actor_user_id=None,
            action="case.resolved",
            target=f"finding:{uuid.uuid4()}",
            detail={"resolution": "no_activity"},
        ),
    ),
}


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_each_statement_binds_the_tenant_and_its_parameters_match(name: str) -> None:
    statement, parameters = STATEMENTS[name]
    binds = set(_BINDS.findall(statement))
    assert "org_id" in binds, f"{name} does not bind the tenant"
    assert set(parameters) == binds, f"{name}'s parameters and binds differ"


def test_a_resolve_moves_only_an_investigating_case() -> None:
    """Idempotence by predicate: a second run, or a race with another resolver,
    matches no row instead of rewriting how the case ended."""
    assert "case_state = 'investigating'" in cases.RESOLVE_CASE_SQL


def test_a_reopen_moves_only_a_resolved_case_and_resets_the_idle_clock() -> None:
    """D74: entering `investigating` by any route sets `case_activity_at`, so a
    reopen is not undone by the next sweep."""
    assert "case_state = 'resolved'" in cases.REOPEN_CASE_SQL
    assert "case_activity_at" in cases.REOPEN_CASE_SQL


def test_a_resolve_clears_the_idle_warning() -> None:
    """A warned case that ends stops warning; the marker must not survive into a
    reopened case and repeat the email."""
    assert "case_warned_at = NULL" in cases.RESOLVE_CASE_SQL
    assert "case_warned_at = NULL" in cases.REOPEN_CASE_SQL


def test_an_unknown_resolution_is_refused() -> None:
    with pytest.raises(ValueError, match="resolution"):
        cases.resolve_case_parameters(
            org_id=ORG, finding_id=uuid.uuid4(), resolution="fixed", at=AT, resolved_by=None
        )


def test_a_detach_touches_only_open_joined_findings() -> None:
    """FR6: closed joined findings stay attached, for the timeline."""
    assert "state = 'open'" in cases.DETACH_OPEN_JOINED_SQL


def test_a_join_never_captures_a_finding_that_holds_or_joined_a_case() -> None:
    assert "case_state IS NULL" in cases.JOIN_OPEN_SQL
    assert "root_finding_id IS NULL" in cases.JOIN_OPEN_SQL


def test_the_audit_detail_is_rendered_json_and_the_actor_may_be_nobody() -> None:
    _, parameters = STATEMENTS["audit"]
    assert parameters["actor_user_id"] is None
    assert parameters["detail"] == '{"resolution": "no_activity"}'


def test_an_unknown_owed_email_is_refused() -> None:
    """D90: only the three case emails may be owed; nothing else can queue mail."""
    with pytest.raises(ValueError, match="kind"):
        cases.email_owed_parameters(org_id=ORG, finding_id=uuid.uuid4(), kind="digest", at=AT)
