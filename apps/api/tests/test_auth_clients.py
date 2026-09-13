"""What a session list is allowed to remember about a device (T64).

These are the privacy decisions in `auth/clients.py`, asserted rather than left
to a docstring. The one that matters most is negative: **the raw User-Agent and
the exact address must not survive the reduction**, because the session list is
readable by whoever holds a session — which, on the screen this exists for, may
be the intruder.
"""

from __future__ import annotations

from typing import Final

import pytest
from starlette.datastructures import Headers

from nexovue_api.auth.clients import (
    ClientDescription,
    client_ip_of,
    client_label,
    describe_client,
    network_of,
)

CHROME_ON_MAC: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
SAFARI_ON_IPHONE: Final = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"
)
EDGE_ON_WINDOWS: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
)
FIREFOX_ON_LINUX: Final = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (CHROME_ON_MAC, "Chrome on macOS"),
        (SAFARI_ON_IPHONE, "Safari on iOS"),
        (EDGE_ON_WINDOWS, "Edge on Windows"),
        (FIREFOX_ON_LINUX, "Firefox on Linux"),
        ("curl/8.7.1", "curl"),
    ],
)
def test_a_user_agent_becomes_a_family_and_a_platform(user_agent: str, expected: str) -> None:
    """The nesting is the part that goes wrong: Edge says Chrome, Chrome says
    Safari, and an iPhone says Mac OS X. First match wins, in that order."""
    assert client_label(user_agent) == expected


@pytest.mark.parametrize("user_agent", [CHROME_ON_MAC, SAFARI_ON_IPHONE, EDGE_ON_WINDOWS])
def test_no_version_or_device_survives_the_reduction(user_agent: str) -> None:
    """**The privacy decision, stated as an assertion.** A build number and a
    device model are what make a User-Agent a cross-site fingerprint, and none
    of them is what a customer needs to recognise their own laptop."""
    label = client_label(user_agent)
    assert label is not None
    for leak in ("537.36", "141.0.0.0", "18_6", "Mozilla", "AppleWebKit", "iPhone", "Intel"):
        assert leak not in label


def test_an_unrecognised_client_is_unknown_rather_than_guessed() -> None:
    """ "Unknown device" is an honest answer somebody can act on. An invented one
    is worse than none, because it reads as a device they do not own."""
    assert client_label("some-internal-agent/1.0") is None
    assert client_label("") is None
    assert client_label(None) is None


def test_an_absurd_user_agent_is_bounded_before_it_is_matched() -> None:
    """No caller can make us scan a megabyte per sign-in."""
    assert client_label("x" * 100_000 + "Chrome/1") is None


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("203.0.113.42", "203.0.113.0/24"),
        ("198.51.100.255", "198.51.100.0/24"),
        ("2001:db8:1234:5678::1", "2001:db8:1234::/48"),
    ],
)
def test_an_address_is_kept_only_as_its_network(address: str, expected: str) -> None:
    """**The second privacy decision.** A precise address is a household or an
    office, published to whoever is already in the account. The network still
    separates "my usual connection" from "somewhere else entirely"."""
    assert network_of(address) == expected


def test_the_exact_address_is_not_recoverable_from_what_is_kept() -> None:
    """Every host on the block reduces to the same string, which is the point."""
    assert network_of("203.0.113.7") == network_of("203.0.113.211")


def test_a_value_that_is_not_an_address_is_not_recorded() -> None:
    """It would have arrived in a header we already decided how far to trust.
    Storing whatever came would make this a field a caller can write into."""
    assert network_of("not-an-address") is None
    assert network_of("203.0.113.42; DROP") is None
    assert network_of(None) is None


def test_a_forwarded_header_is_ignored_when_no_proxy_is_trusted() -> None:
    """With no trusted proxies the header is a value the client chose, so
    believing it would let anybody write any network into their own list."""
    headers = Headers({"x-forwarded-for": "9.9.9.9"})
    assert (
        client_ip_of(headers, direct_peer="203.0.113.42", trusted_proxy_count=0) == "203.0.113.42"
    )


def test_a_forwarded_header_is_read_as_far_as_the_deployment_says() -> None:
    headers = Headers({"x-forwarded-for": "203.0.113.42, 10.0.0.1"})
    assert client_ip_of(headers, direct_peer="10.0.0.2", trusted_proxy_count=1) == "10.0.0.1"
    assert client_ip_of(headers, direct_peer="10.0.0.2", trusted_proxy_count=2) == "203.0.113.42"


def test_a_request_reduces_to_exactly_two_fields() -> None:
    """`ClientDescription` is the whole of what a session remembers. A third
    field is a decision, and adding one should fail this."""
    described = describe_client(Headers({"user-agent": CHROME_ON_MAC}), "203.0.113.42")

    assert described == ClientDescription(label="Chrome on macOS", network="203.0.113.0/24")
    assert set(ClientDescription.__slots__) == {"label", "network"}


def test_a_request_that_said_nothing_describes_nothing() -> None:
    assert describe_client(Headers({}), None) == ClientDescription.unknown()
