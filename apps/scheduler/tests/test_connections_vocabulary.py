"""The connection vocabulary both services share (F22 plan T3.1).

The API adds connection jobs by name and the scheduler runs them; the queue names,
the cap, the task names, the sealing purposes and the state words live once in
`nexovue_platform.connections`, so neither side can drift from the other. A link's
address is built, never stored (D81), and refuses a key that is not its strict
shape.
"""

from __future__ import annotations

import pytest
from nexovue_platform import connections


def test_the_two_queues_and_the_cap_are_f5_s_layout() -> None:
    """Design, *Roles, queues and the task set*: sync on a capped queue, 2 of 3 slots."""
    assert connections.CONNECTIONS_QUEUE == "connections"
    assert connections.CONNECTIONS_INTERACTIVE_QUEUE == "connections-interactive"
    assert connections.CONNECTIONS_QUEUE_CAP == 2
    assert connections.CONNECTIONS_CONCURRENCY == 3
    assert connections.CONNECTIONS_QUEUE_CAP < connections.CONNECTIONS_CONCURRENCY


def test_every_task_name_is_the_scheduler_s_connections_package() -> None:
    names = connections.CONNECTION_TASKS
    assert len(set(names)) == len(names)
    assert all(name.startswith("nexovue_scheduler.connections.") for name in names)
    assert {
        connections.CONVERT_MANIFEST_TASK,
        connections.REFRESH_CONNECTION_TASK,
        connections.CHECK_CONNECTION_TASK,
        connections.SYNC_CONNECTION_TASK,
        connections.SWEEP_CONNECTIONS_TASK,
    } == set(names)


def test_kinds_hold_f5_s_app_beside_the_two_trackers() -> None:
    assert connections.CONNECTION_KINDS == ("github", "jira", "github_fix")
    assert connections.TRACKER_KINDS == ("github", "jira")


def test_states_are_the_spec_s_table() -> None:
    assert connections.CONNECTION_STATES == (
        "setting_up",
        "working",
        "failing",
        "needs_attention",
        "disconnected",
    )
    assert connections.LIVE_STATES == ("setting_up", "working", "failing", "needs_attention")


def test_link_statuses_are_the_spec_s_table() -> None:
    assert connections.LINK_STATUSES == (
        "creating",
        "promoting",
        "in_sync",
        "waiting",
        "needs_attention",
        "refused",
        "gone",
        "not_followed",
        "stopped",
    )


def test_each_sealed_value_has_its_own_purpose_bound_to_the_connection() -> None:
    purposes = (
        connections.CONNECTION_CREDENTIAL_PURPOSE,
        connections.CONNECTION_WEBHOOK_SECRET_PURPOSE,
        connections.CONNECTION_MANIFEST_CODE_PURPOSE,
    )
    assert len(set(purposes)) == 3
    assert connections.connection_secret_owner("org", "conn") == "org:conn"


def test_a_job_s_arguments_are_ids_only() -> None:
    """AC74: a job row never holds the code or a key; the job opens what it needs."""
    assert connections.connection_job_arguments(org_id="o", connection_id="c") == {
        "org_id": "o",
        "connection_id": "c",
    }
    assert connections.connection_test_arguments(org_id="o", connection_id="c", request_id="r") == {
        "org_id": "o",
        "connection_id": "c",
        "request_id": "r",
    }


def test_every_failure_has_a_known_word() -> None:
    assert len(set(connections.CONNECTION_FAILURES)) == len(connections.CONNECTION_FAILURES)
    assert all(word.isidentifier() for word in connections.CONNECTION_FAILURES)


@pytest.mark.parametrize(
    ("kind", "site", "key", "url"),
    [
        ("github", None, "acme/api#412", "https://github.com/acme/api/issues/412"),
        ("github", None, "my-org/web.site#1", "https://github.com/my-org/web.site/issues/1"),
        ("jira", "acme.atlassian.net", "OPS-12", "https://acme.atlassian.net/browse/OPS-12"),
    ],
)
def test_a_link_s_address_is_built_from_its_key(
    kind: str, site: str | None, key: str, url: str
) -> None:
    assert connections.link_url(kind, site, key) == url


@pytest.mark.parametrize(
    ("kind", "site", "key"),
    [
        ("github", None, "acme/api#412/../../x"),
        ("github", None, "evil.example/a#1"),
        ("github", None, "acme/api#"),
        ("github", None, "acme/api"),
        ("github", None, "acme/../api#1"),
        ("jira", "acme.atlassian.net", "OPS-1/../../myself"),
        ("jira", "acme.atlassian.net", "ops-12"),
        ("jira", "evil.example", "OPS-12"),
        ("jira", None, "OPS-12"),
        ("github_fix", None, "acme/api#1"),
    ],
)
def test_a_key_or_site_outside_its_strict_shape_builds_nothing(
    kind: str, site: str | None, key: str
) -> None:
    with pytest.raises(ValueError, match="not"):
        connections.link_url(kind, site, key)


@pytest.mark.parametrize(
    "key",
    ["acme/api", "a-b/c.d_e", "Org1/repo-2"],
)
def test_a_github_repository_key_has_its_strict_shape(key: str) -> None:
    assert connections.is_repository_key(key)


@pytest.mark.parametrize("key", ["acme", "acme/api/x", "acme/..", "-acme/api", "a b/c", "acme/.."])
def test_anything_else_is_not_a_repository_key(key: str) -> None:
    assert not connections.is_repository_key(key)
