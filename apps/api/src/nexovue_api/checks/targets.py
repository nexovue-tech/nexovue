"""What a check is allowed to point at.

A check makes *our* infrastructure fetch a URL the customer chose, and report
back the status, the timings and the certificate. That is a request forgery
primitive if it is left unguarded: `http://169.254.169.254/` is the cloud
metadata service, `http://postgres:5432` is our own database, and either would
be probed from inside our network with the result handed back to whoever asked.

So a target must be a public HTTP or HTTPS address. Everything else is refused
at creation, with a message that says why rather than a generic rejection.

**This is not the security boundary, and it cannot be.** Validation happens
here and the fetch happens minutes later in another service, so a hostname that
resolves publicly now can resolve to a private address then — DNS rebinding,
which nothing decided at creation time can see.

The boundary is `nexovue_scheduler.probes.addresses`, which refuses the socket
at the moment libcurl has resolved an address and is about to connect to it.
That is the only point where the address cannot change again.

What stays here is a fast, specific rejection: a customer who pastes a loopback
URL is told so while they are looking at the form, rather than waiting for a
check that fails for a reason they have to go and read. So these two rules may
drift without opening a hole — this one being more permissive costs a worse
error message, and being stricter costs nothing.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

from nexovue_api.errors.exceptions import InvalidRequestError

ALLOWED_SCHEMES: Final = frozenset({"http", "https"})

# Long enough for a real health endpoint with query parameters, short enough
# that the column and the log line stay reasonable.
MAX_TARGET_LENGTH: Final = 2048

# Hostnames that name our own deployment. A customer pointing a check at one
# would be measuring our internals and getting the answer back.
INTERNAL_HOSTNAMES: Final = frozenset(
    {"localhost", "postgres", "clickhouse", "kafka", "redis", "api", "receiver", "worker"}
)


def validate_target(raw: str) -> str:
    """Return the target to store, or raise if it is not one we will fetch."""
    target = raw.strip()
    if not target:
        raise InvalidRequestError("A check needs a target URL.")
    if len(target) > MAX_TARGET_LENGTH:
        raise InvalidRequestError(f"A target URL may be at most {MAX_TARGET_LENGTH} characters.")

    parts = urlsplit(target)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidRequestError("A target URL must begin with http:// or https://.")

    host = (parts.hostname or "").lower()
    if not host:
        raise InvalidRequestError("A target URL must include a hostname.")
    if host in INTERNAL_HOSTNAMES:
        raise InvalidRequestError("That hostname names nexovue's own infrastructure.")

    _refuse_private_address(host)
    return target


def validate_host(raw: str) -> str:
    """A TCP host: the same refusals as a URL's host, without the URL around it."""
    host = raw.strip().lower().strip("[]")
    if not host:
        raise InvalidRequestError("A check needs a host.")
    if host in INTERNAL_HOSTNAMES:
        raise InvalidRequestError("That hostname names nexovue's own infrastructure.")
    _refuse_private_address(host)
    return raw.strip()


def validate_resolver(raw: str) -> str:
    """A DNS resolver: a public address, never a name.

    A name would have to be resolved before it could be asked anything, and a
    resolver inside our network would answer questions about it.
    """
    address = raw.strip().strip("[]")
    try:
        ipaddress.ip_address(address)
    except ValueError as not_an_address:
        raise InvalidRequestError("A resolver must be an IP address.") from not_an_address
    _refuse_private_address(address)
    return address


def _refuse_private_address(host: str) -> None:
    """Refuse an address that is not routable on the public internet.

    Only literal addresses are decided here. A name is left alone because
    resolving it would answer a question about *now*, and the fetch happens
    later — see the module docstring.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # a hostname, not a literal address

    if address.is_private or address.is_loopback or address.is_link_local:
        # Link-local covers 169.254.0.0/16, which is where every major cloud
        # serves instance credentials.
        raise InvalidRequestError("A target must be a public address, not a private one.")
    if address.is_reserved or address.is_multicast or address.is_unspecified:
        raise InvalidRequestError("That is not an address a check can reach.")
    if not address.is_global:
        # Carrier-grade NAT (100.64.0.0/10) is not reported as private by
        # Python, and it carries GKE pod CIDRs and Alibaba's metadata endpoint.
        # Same pairing as `nexovue_scheduler.probes.addresses`, which is the
        # boundary this message exists to pre-empt.
        raise InvalidRequestError("A target must be a public address, not a private one.")
