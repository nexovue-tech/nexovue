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
from dataclasses import dataclass
from typing import Any, Final

import pycurl

# libcurl's own name for a socket it cannot use. Returning it aborts the
# connection attempt without raising through the C stack.
REFUSED: Final = pycurl.SOCKET_BAD

REFUSAL_REASON: Final = "The target resolved to a non-public address and was not contacted."


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


IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# The widest range an operator may list (AC37: "a very wide range" is refused). Guesses: a
# /16 is a whole office network, wider than any one model server needs.
WIDEST_IPV4_PREFIX: Final = 16
WIDEST_IPV6_PREFIX: Final = 48


def _never_reachable(target: IPAddress | IPNetwork) -> bool:
    """What stays refused even when listed: this container, the cloud's credentials
    address, and ranges that route nowhere a model server lives (AC37)."""
    return bool(
        target.is_loopback
        or target.is_link_local
        or target.is_reserved
        or target.is_multicast
        or target.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class PrivateEndpoints:
    """The private addresses, each with a port, an install operator lets an AI provider
    connection reach (`AI_PRIVATE_ENDPOINTS`, FR32; design *Provider interface*).

    **This widens this module's promise** that a private address is reached only in
    development, for one purpose: a local model on the customer's own network. Only an
    operator can set it; a console user cannot. Loopback, link-local, reserved, multicast and
    unspecified addresses stay refused even when listed, so the instance-credentials address
    stays out of reach, and only an address that is private is ever let through.
    """

    entries: tuple[tuple[IPNetwork, int], ...] = ()

    @classmethod
    def parse(cls, configured: str) -> PrivateEndpoints:
        """`10.0.0.5:11434,192.168.1.0/24:8000,[fd00::7]:11434`. Raises ValueError naming
        what is wrong with an entry, so settings stop the process on a typo (AC37)."""
        entries = []
        for raw in (entry.strip() for entry in configured.split(",")):
            if raw:
                entries.append(_endpoint(raw))
        return cls(tuple(entries))

    def allows(self, address_text: str, port: int) -> bool:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            return False
        if _never_reachable(address) or not address.is_private:
            return False
        return any(address in network and port == allowed for network, allowed in self.entries)


NO_PRIVATE_ENDPOINTS: Final = PrivateEndpoints()


def _endpoint(raw: str) -> tuple[IPNetwork, int]:
    if raw.startswith("["):
        host, separator, port_text = raw[1:].partition("]:")
    else:
        host, separator, port_text = raw.rpartition(":")
    if not separator or not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise ValueError(f"{raw!r} needs a port, as address:port")
    try:
        network = ipaddress.ip_network(host, strict=True)
    except ValueError as unreadable:
        raise ValueError(f"{raw!r} is not an address or a range") from unreadable
    if _never_reachable(network) or not network.is_private:
        raise ValueError(f"{raw!r} is not wholly private, or is loopback or link-local")
    widest = WIDEST_IPV4_PREFIX if network.version == 4 else WIDEST_IPV6_PREFIX
    if network.prefixlen < widest:
        raise ValueError(f"{raw!r} is wider than /{widest}; list the model server's address")
    return network, int(port_text)


def refusal_reason_for(refused_addresses: Iterable[str]) -> str | None:
    """The error to report instead of the client's, when we caused the failure.

    None when nothing was refused, so an ordinary failure keeps its own message
    rather than gaining a false one. Shared by every probe, so a refusal reads the
    same for a request, a TCP connection and a DNS resolver.

    **The address is not in it.** This message is shown to the customer, and the
    address a name resolved to from inside our network — `postgres` to
    172.18.0.4 — is a fact about our network, not theirs.
    """
    if not any(True for _ in refused_addresses):
        return None
    return REFUSAL_REASON


class ConnectGuard:
    """Opens sockets for libcurl, refusing any address that is not public.

    Remembers what it refused so the probe can report *why* it failed. Without
    that, a rebinding attempt is indistinguishable from a host being down, and
    the one result that should be investigated looks like the most ordinary one.
    """

    def __init__(
        self,
        *,
        private_allowed: bool = False,
        private_endpoints: PrivateEndpoints = NO_PRIVATE_ENDPOINTS,
        public_allowed: bool = True,
    ) -> None:
        self.refused_addresses: list[str] = []
        # Passed in rather than read from the environment here, because every
        # other collaborator of a probe is passed in and a hidden module-level
        # read is the kind of thing that survives into production unnoticed.
        self._private_allowed = private_allowed
        # F3: an operator's listed endpoints, and whether a public address may be reached
        # at all (an AI call over plain `http` may reach a listed address and nothing else).
        self._private_endpoints = private_endpoints
        self._public_allowed = public_allowed

    def _may_open(self, resolved: str, port: int) -> bool:
        if self._private_endpoints.allows(resolved, port):
            return True
        return self._public_allowed and may_connect_to(
            resolved, private_allowed=self._private_allowed
        )

    def open_socket(self, purpose: int, address: Any) -> Any:
        """libcurl's socket factory. `address.addr` is the resolved host and port."""
        del purpose  # libcurl distinguishes proxy sockets; the rule is the same
        resolved = str(address.addr[0])
        if not self._may_open(resolved, int(address.addr[1])):
            self.refused_addresses.append(resolved)
            return REFUSED
        return socket.socket(address.family, address.socktype, address.protocol)

    def refusal_reason(self) -> str | None:
        """The error to report instead of libcurl's, when we caused the failure."""
        return refusal_reason_for(self.refused_addresses)
