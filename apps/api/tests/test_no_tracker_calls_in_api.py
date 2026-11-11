"""The API calls no tracker (F22 AC32, design *Where this differs from the foundation*).

Every GitHub and Jira call, a connection test included, runs in `scheduler-connections`,
behind the connect-time address guard the API does not have. The API adds jobs and reads
their results. So no module of `nexovue_api` may import an HTTP client, except the ones
named below with their reason. A new client is a decision, made here, not a line slipped
into a router.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.import_walk import NETWORK_CLIENTS, direct_imports_matching, package_modules

# The only modules allowed a network client, each with why. AC32 names the payment client
# alone; the install's own object store was found holding boto3 when this was built, and
# stays here pending the founder's word on AC32's list (F22 status.md, *Open decisions*).
ALLOWED: Final[dict[str, str]] = {
    "nexovue_api.billing.razorpay": "the payment processor's own API, nexovue's account (F31)",
    "nexovue_api.data.objectstore.store": "the install's own S3-compatible store (source maps)",
    "nexovue_api.backups": "the install's own S3-compatible store, for a snapshot's files",
}


def test_no_api_module_but_the_named_ones_holds_an_http_client() -> None:
    modules = package_modules("nexovue_api")
    assert "nexovue_api.connections.router" in modules, "the walk must cover the connections"

    found = {
        line.rsplit(" <- ", 1)[-1] for line in direct_imports_matching(modules, NETWORK_CLIENTS)
    }

    assert found - set(ALLOWED) == set(), "an API module calls out over HTTP"


def test_every_allowance_still_names_a_module_that_uses_a_client() -> None:
    """A stale allowance would let the next module of that name call out unseen."""
    for module in ALLOWED:
        assert direct_imports_matching([module], NETWORK_CLIENTS), f"{module} holds no client"


def test_the_rule_sees_a_client_a_module_imports() -> None:
    """The control: the same lookup must find a client where one is imported."""
    found = direct_imports_matching(["nexovue_api.billing.razorpay"], NETWORK_CLIENTS)
    assert found and all(" <- nexovue_api.billing.razorpay" in line for line in found)
