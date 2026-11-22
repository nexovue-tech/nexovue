"""Whether a draft may be opened for a repository, by one rule in one order (F5 plan T7.4,
T7.5; spec FR22, BR13, BR14, D58, AC27a, AC60)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from nexovue_platform import evidence
from nexovue_platform.fixes.draft import (
    DRAFT_CAUSE_RULES,
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
