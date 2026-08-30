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
    """
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return False

    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


class ConnectGuard:
    """Opens sockets for libcurl, refusing any address that is not public.

    Remembers what it refused so the probe can report *why* it failed. Without
    that, a rebinding attempt is indistinguishable from a host being down, and
    the one result that should be investigated looks like the most ordinary one.
    """

    def __init__(self) -> None:
        self.refused_addresses: list[str] = []

    def open_socket(self, purpose: int, address: Any) -> Any:
        """libcurl's socket factory. `address.addr[0]` is the resolved host."""
        del purpose  # libcurl distinguishes proxy sockets; the rule is the same
        resolved = str(address.addr[0])
        if not is_publicly_routable(resolved):
            self.refused_addresses.append(resolved)
            return REFUSED
        return socket.socket(address.family, address.socktype, address.protocol)

    def refusal_reason(self) -> str | None:
        """The error to report instead of libcurl's, when we caused the failure."""
        if not self.refused_addresses:
            return None
        addresses = ", ".join(dict.fromkeys(self.refused_addresses))
        return f"Target resolved to a non-public address and was not contacted: {addresses}"
