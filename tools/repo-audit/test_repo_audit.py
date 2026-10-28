"""`repo_audit.drift` against recorded API answers, so the audit is proved without a network."""

from __future__ import annotations

import json
from typing import Any

from repo_audit import EXPECTED, drift

# The API's answers on 2026-10-28, after Step 0's settings were applied (trimmed to what is read).
APPLIED: dict[str, tuple[int, Any]] = {
    "": (
        200,
        {
            "private": True,
            "has_wiki": False,
            "has_projects": False,
            "delete_branch_on_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "allow_squash_merge": True,
            "allow_update_branch": True,
            "web_commit_signoff_required": True,
        },
    ),
    "/actions/permissions": (
        200,
        {"enabled": True, "allowed_actions": "selected", "sha_pinning_required": True},
    ),
    "/actions/permissions/selected-actions": (
        200,
        {"github_owned_allowed": True, "verified_allowed": True, "patterns_allowed": []},
    ),
    "/actions/permissions/workflow": (
        200,
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
    ),
    "/automated-security-fixes": (200, {"enabled": True, "paused": False}),
    "/vulnerability-alerts": (204, None),
    "@/orgs/nexovue-tech": (
        200,
        {
            "default_repository_permission": "read",
            "members_can_create_repositories": False,
            "members_can_create_public_repositories": False,
            "members_can_create_private_repositories": False,
            "members_can_change_repo_visibility": False,
            "two_factor_requirement_enabled": True,
        },
    ),
}


def _expected() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(EXPECTED.read_text())
    return loaded


def test_the_applied_settings_pass() -> None:
    assert drift(_expected(), APPLIED.__getitem__) == []


def test_a_repository_made_public_is_drift() -> None:
    answers = dict(APPLIED)
    answers[""] = (200, {**APPLIED[""][1], "private": False})

    assert drift(_expected(), answers.__getitem__) == ["/ private: expected True, found False"]


def test_alerts_switched_off_are_drift() -> None:
    answers = dict(APPLIED)
    answers["/vulnerability-alerts"] = (404, None)

    assert drift(_expected(), answers.__getitem__) == [
        "/vulnerability-alerts: expected enabled, the API answered 404"
    ]


def test_an_unreadable_setting_is_drift_not_a_pass() -> None:
    answers = dict(APPLIED)
    answers["/actions/permissions/workflow"] = (403, None)

    assert drift(_expected(), answers.__getitem__) == [
        "/actions/permissions/workflow: the API answered 403"
    ]


def test_members_allowed_to_publish_a_repository_is_drift() -> None:
    answers = dict(APPLIED)
    answers["@/orgs/nexovue-tech"] = (
        200,
        {**APPLIED["@/orgs/nexovue-tech"][1], "members_can_change_repo_visibility": True},
    )

    assert drift(_expected(), answers.__getitem__) == [
        "@/orgs/nexovue-tech members_can_change_repo_visibility: expected False, found True"
    ]
