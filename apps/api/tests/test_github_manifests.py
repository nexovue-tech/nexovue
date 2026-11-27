"""The App manifests GitHub is asked to register (F22 and F5).

The founder's live test against github.com, 2026-11-27: GitHub refuses a webhook URL it
cannot reach, even with `active: false` ("Hook url is not supported because it isn't reachable
over the public Internet (localhost)"), and an App that subscribes to events without a webhook
("Hook url cannot be blank"). So an App that wants no deliveries asks for neither.
"""

from __future__ import annotations

import uuid

from nexovue_api.connections.fix_app import fix_app_manifest
from nexovue_api.connections.service import github_manifest

LOCAL = "http://localhost:15173"


def test_an_unreachable_install_asks_github_for_no_webhook() -> None:
    manifest = github_manifest(
        base_url=LOCAL, connection_id=uuid.uuid4(), organization_slug="acme", reachable=False
    )

    assert "hook_attributes" not in manifest
    assert manifest["default_events"] == []


def test_a_reachable_install_asks_github_for_its_webhook() -> None:
    connection_id = uuid.uuid4()
    manifest = github_manifest(
        base_url="https://nexovue.example.com",
        connection_id=connection_id,
        organization_slug="acme",
        reachable=True,
    )

    assert manifest["hook_attributes"] == {
        "url": f"https://nexovue.example.com/api/v1/connections/webhook/{connection_id}",
        "active": True,
    }
    assert manifest["default_events"] == ["issues"]


def test_the_suggestions_app_asks_github_for_no_webhook() -> None:
    manifest = fix_app_manifest(base_url=LOCAL, organization_slug="acme")

    assert "hook_attributes" not in manifest
