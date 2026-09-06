"""Which addresses a probe is allowed to connect to, decided at connect time.

The API refuses a private target when a check is created. That check cannot be
the security boundary: it runs minutes before the fetch, and a hostname that
resolves publicly then can resolve to `169.254.169.254` now. Re-resolving before
connecting does not close it either — libcurl would resolve again afterwards,
and the answer could differ. That is DNS rebinding.

The only address that cannot change between the decision and the connection is
the one libcurl is holding when it asks for a socket. `OPENSOCKETFUNCTION` is
that moment, and refusing there is the boundary.

**libcurl offers each resolved address in turn**, so this is asked once per
candidate rather than once per request. A name resolving to one public and one
private address connects to the public one and never touches the private one.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any, Final

import pycurl

# libcurl's own name for a socket it cannot use. Returning it aborts the
# connection attempt without raising through the C stack.
REFUSED: Final = pycurl.SOCKET_BAD


def is_publicly_routable(address_text: str) -> bool:
    """Whether an address is one the public internet can reach.

    Anything else is refused: private ranges are the customer's own network or
    ours, loopback is this container, and link-local is 169.254.0.0/16, where
    every major cloud serves instance credentials.

    An address that will not parse is refused rather than allowed. This is asked
    on the path to a connection, so the safe answer to "I do not recognise this"
    is no.

    Two tests, and it must be both. The named flags carry the intent and catch
    what `is_global` alone would let through — multicast, and `64:ff9b::/96`
    wrapping a private v4 address, which is how NAT64 reaches 169.254.169.254
    in one hop. `is_global` catches what the named flags miss: Python stopped
    reporting carrier-grade NAT (100.64.0.0/10) as private, and that range
    carries GKE pod CIDRs and Alibaba's metadata endpoint at 100.100.100.200.
    Neither test is a superset of the other, so narrowing to one reopens
    whatever the other was holding.
    """
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return False

    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def may_connect_to(address_text: str, *, private_allowed: bool) -> bool:
    """Whether a probe may open a socket to this address.

    A second question beside `is_publicly_routable`, not a change to it. That one
    answers "can the public internet reach this", which several suites exist to
    hold to its word; widening it would silently defeat them.

    `private_allowed` is development only and widens exactly one thing: the
    ranges the Docker host lives in, because that is where `just otel-demo up`
    publishes the demo storefront and a probe has no other way to reach it.

    **Loopback and link-local stay refused even then**, and that is the line
    worth defending. Loopback is the worker's own container, link-local is
    169.254.169.254 where every major cloud serves instance credentials, and
    neither becomes safe to probe because somebody is developing. The one thing
    this opens is a private network the developer is already inside.
    """
    if is_publicly_routable(address_text):
        return True
    if not private_allowed:
        return False

    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        # Unparseable stays refused on the path to a connection, for the reason
        # `is_publicly_routable` gives: the safe answer to "I do not recognise
        # this" is no.
        return False

    return address.is_private and not (
        address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def refusal_reason_for(refused_addresses: Iterable[str]) -> str | None:
    """The error to report instead of the client's, when we caused the failure.

    None when nothing was refused, so an ordinary failure keeps its own message
    rather than gaining a false one. Shared by every guard, so a refusal reads
    the same whether libcurl or Chromium was the one asking.
    """
    addresses = ", ".join(dict.fromkeys(refused_addresses))
    if not addresses:
        return None
    return f"Target resolved to a non-public address and was not contacted: {addresses}"


class ConnectGuard:
    """Opens sockets for libcurl, refusing any address that is not public.

    Remembers what it refused so the probe can report *why* it failed. Without
    that, a rebinding attempt is indistinguishable from a host being down, and
    the one result that should be investigated looks like the most ordinary one.
    """

    def __init__(self, *, private_allowed: bool = False) -> None:
        self.refused_addresses: list[str] = []
        # Passed in rather than read from the environment here, because every
        # other collaborator of a probe is passed in and a hidden module-level
        # read is the kind of thing that survives into production unnoticed.
        self._private_allowed = private_allowed

    def open_socket(self, purpose: int, address: Any) -> Any:
        """libcurl's socket factory. `address.addr[0]` is the resolved host."""
        del purpose  # libcurl distinguishes proxy sockets; the rule is the same
        resolved = str(address.addr[0])
        if not may_connect_to(resolved, private_allowed=self._private_allowed):
            self.refused_addresses.append(resolved)
            return REFUSED
        return socket.socket(address.family, address.socktype, address.protocol)

    def refusal_reason(self) -> str | None:
        """The error to report instead of libcurl's, when we caused the failure."""
        return refusal_reason_for(self.refused_addresses)
