"""Whether a draft may be opened for a repository, by one rule in one order (F5 plan T7.4,
T7.5; spec FR22, BR13, BR14, D58, AC27a, AC60); and Run CI's own gates (T8.1, FR25)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from nexovue_platform import evidence
from nexovue_platform.fixes.draft import (
    DRAFT_CAUSE_RULES,
    ci_done,
    ci_refusal,
    draft_refusal,
    has_write_grant,
    public_refused,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
WRITE = {"contents": "write", "pull_requests": "write", "metadata": "read"}


def _refusal(**overrides: Any) -> str | None:
    values: dict[str, Any] = {
        "drafts_allowed": True,
        "granted": WRITE,
        "draft_refused_at": None,
        "signed_deploy_cause": True,
        "now": NOW,
    }
    return draft_refusal(**(values | overrides))


def test_with_every_condition_met_nothing_refuses() -> None:
    assert _refusal() is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"drafts_allowed": False, "granted": None}, "drafts_not_allowed"),
        ({"granted": {"contents": "read"}, "signed_deploy_cause": False}, "no_write_permission"),
        ({"draft_refused_at": NOW - timedelta(hours=23)}, "no_draft_support"),
        ({"signed_deploy_cause": False}, "needs_likely_cause"),
    ],
)
def test_the_first_refusal_in_order_is_the_one_said(
    overrides: dict[str, Any], expected: str
) -> None:
    assert _refusal(**overrides) == expected


def test_ac27a_a_refusal_by_github_holds_for_24_hours_only() -> None:
    assert _refusal(draft_refused_at=NOW - timedelta(hours=24)) is None


@pytest.mark.parametrize(
    ("granted", "has"),
    [
        (WRITE, True),
        ({"contents": "admin", "pull_requests": "write"}, True),
        ({"contents": "write"}, False),
        ({"contents": "read", "pull_requests": "write"}, False),
        (None, False),
        ({}, False),
    ],
)
def test_the_app_needs_both_write_permissions(granted: Any, has: bool) -> None:
    assert has_write_grant(granted) is has


def test_br13_only_a_public_repository_without_an_allowance_is_refused() -> None:
    assert public_refused(private=False, public_allowed=False)
    assert not public_refused(private=False, public_allowed=True)
    assert not public_refused(private=True, public_allowed=False)


def test_d58_the_draft_rules_are_f3_s_own_signed_deploy_rules() -> None:
    assert {evidence.RULE_SIGNED_DEPLOY, evidence.RULE_SIGNED_DEPLOY_WEAK} == DRAFT_CAUSE_RULES


@pytest.mark.parametrize(
    ("drafts_allowed", "granted", "expected"),
    [
        (True, WRITE, None),
        (False, WRITE, "drafts_not_allowed"),
        (True, {"contents": "read", "pull_requests": "write"}, "no_write_permission"),
        (True, None, "no_write_permission"),
    ],
)
def test_fr25_run_ci_needs_the_owner_s_allowance_and_the_app_s_write_grant(
    drafts_allowed: bool, granted: dict[str, str] | None, expected: str | None
) -> None:
    assert ci_refusal(drafts_allowed=drafts_allowed, granted=granted) == expected


def test_fr25_a_draft_github_refused_or_its_cause_do_not_stop_run_ci() -> None:
    """Those decide whether a draft may open; Run CI acts on one already open."""
    assert ci_refusal(drafts_allowed=True, granted=WRITE) is None
    assert _refusal(draft_refused_at=NOW, signed_deploy_cause=False) == "no_draft_support"


def test_fr25_run_ci_is_done_only_for_the_request_the_row_holds_now() -> None:
    earlier, later = NOW - timedelta(minutes=5), NOW
    assert ci_done(requested_at=later, commit_requested_at=later, pushed_at=later)
    assert not ci_done(requested_at=later, commit_requested_at=later, pushed_at=None)
    assert not ci_done(requested_at=later, commit_requested_at=earlier, pushed_at=earlier)
    assert not ci_done(requested_at=None, commit_requested_at=None, pushed_at=None)
