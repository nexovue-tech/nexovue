"""The connect-time address guard: DNS rebinding, closed at the socket.

The API refuses a private *target* when a check is created. That is not the
boundary, and this suite proves it is not: `127.0.0.1.nip.io` is a public name
that resolves to loopback, and the creation-time validator accepts it, because
at creation it is a name and not an address.

The guard is asked at the one moment the address cannot change again — when
libcurl has resolved a candidate and wants a socket for it.

The unit tests need no network. The live one resolves real names, and fails
rather than skips when it cannot, for the reason `live.py` gives.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any, Final

import pytest
from live import require_stack

from nexovue_scheduler.probes.addresses import REFUSED, ConnectGuard, is_publicly_routable

# Names that resolve to a fixed address by design, so the rebinding case can be
# exercised against real DNS rather than a mock of it.
LOOPBACK_NAME: Final = "127.0.0.1.nip.io"
METADATA_NAME: Final = "169.254.169.254.nip.io"


def _dns_works() -> bool:
    try:
        socket.getaddrinfo(LOOPBACK_NAME, 80)
    except OSError:
        return False
    return True


require_stack(
    reachable=_dns_works(),
    suite="the connect guard's live rebinding test",
    remedy=f"{LOOPBACK_NAME} must resolve, which needs working DNS and internet access",
)


@dataclass
class FakeSockAddr:
    """The shape libcurl hands the callback: family, socktype, protocol, addr."""

    addr: tuple[Any, ...]
    family: int = socket.AF_INET
    socktype: int = socket.SOCK_STREAM
    protocol: int = 0


# ------------------------------------------------------------------ the ruling


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # this container
        "::1",
        "10.0.0.5",  # private ranges: the customer's network, or ours
        "172.16.4.4",
        "192.168.1.1",
        "169.254.169.254",  # every major cloud serves instance credentials here
        "fd00::1",  # unique-local, the IPv6 equivalent of private
        "0.0.0.0",
        "224.0.0.1",  # multicast
        # Carrier-grade NAT. Python does not report 100.64.0.0/10 as private,
        # so the named flags alone let it through — and it carries GKE pod
        # CIDRs and Alibaba's metadata endpoint.
        "100.64.0.1",
        "100.100.100.200",
        # NAT64 reaching the metadata address in one hop. `is_global` alone
        # would allow these, which is why the check is both tests, not one.
        "64:ff9b::a9fe:a9fe",
        "64:ff9b::a00:1",
        "",
        "not-an-address",
    ],
)
def test_an_address_the_internet_cannot_reach_is_refused(address: str) -> None:
    assert not is_publicly_routable(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "1.1.1.1", "2606:4700::1111"])
def test_a_public_address_is_allowed(address: str) -> None:
    assert is_publicly_routable(address)


def test_an_unparseable_address_is_refused_rather_than_allowed() -> None:
    """This is asked on the path to a connection; unrecognised must mean no."""
    assert not is_publicly_routable("2606:4700::gggg")


# ------------------------------------------------------------------- the guard


def test_the_guard_refuses_the_socket_for_a_private_address() -> None:
    guard = ConnectGuard()

    result = guard.open_socket(0, FakeSockAddr(addr=("169.254.169.254", 80)))

    assert result == REFUSED
    assert guard.refused_addresses == ["169.254.169.254"]


def test_the_guard_opens_a_real_socket_for_a_public_address() -> None:
    guard = ConnectGuard()

    result = guard.open_socket(0, FakeSockAddr(addr=("93.184.216.34", 80)))

    assert isinstance(result, socket.socket)
    assert guard.refused_addresses == []
    result.close()


def test_every_candidate_is_judged_not_only_the_first() -> None:
    """libcurl offers each resolved address in turn; one ruling is not enough."""
    guard = ConnectGuard()

    for candidate in ("2606:4700::1111", "10.0.0.1", "169.254.169.254"):
        guard.open_socket(0, FakeSockAddr(addr=(candidate, 80)))

    assert guard.refused_addresses == ["10.0.0.1", "169.254.169.254"]


def test_a_refusal_reports_why_rather_than_looking_like_a_host_being_down() -> None:
    """libcurl would say "could not connect", which is the wrong diagnosis."""
    guard = ConnectGuard()
    guard.open_socket(0, FakeSockAddr(addr=("169.254.169.254", 80)))

    reason = guard.refusal_reason()

    assert reason is not None
    assert "169.254.169.254" in reason
    assert "not contacted" in reason


def test_a_probe_that_refused_nothing_reports_no_reason_of_its_own() -> None:
    """An ordinary failure must keep libcurl's message, not gain a false one."""
    assert ConnectGuard().refusal_reason() is None


def test_the_same_address_offered_twice_is_named_once() -> None:
    guard = ConnectGuard()
    for _ in range(3):
        guard.open_socket(0, FakeSockAddr(addr=("10.0.0.1", 80)))

    assert guard.refusal_reason() == (
        "Target resolved to a non-public address and was not contacted: 10.0.0.1"
    )


# -------------------------------------------------------------------- live DNS


@pytest.mark.parametrize("name", [LOOPBACK_NAME, METADATA_NAME])
def test_a_real_public_name_resolving_privately_is_refused_at_connect(name: str) -> None:
    """The rebinding case end to end, against real DNS.

    Without the guard this reaches the address: measured, an unguarded probe to
    the metadata name spent five seconds actually attempting the connection.
    """
    from nexovue_scheduler.probes import http_check

    result = http_check.perform("rebind", f"http://{name}/", 5)

    assert result.up is False
    assert result.error is not None
    assert "not contacted" in result.error
