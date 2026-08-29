"""What a check is allowed to point at.

A check makes *our* infrastructure fetch a URL the customer chose, and report
back the status, the timings and the certificate. That is a request forgery
primitive if it is left unguarded: `http://169.254.169.254/` is the cloud
metadata service, `http://postgres:5432` is our own database, and either would
be probed from inside our network with the result handed back to whoever asked.

So a target must be a public HTTP or HTTPS address. Everything else is refused
at creation, with a message that says why rather than a generic rejection.

**This is a floor, not a ceiling.** Validation happens here, and the fetch
happens minutes later in another service — so a hostname that resolves publicly
now can resolve to a private address then, which is DNS rebinding and this
cannot see it. Closing that needs the prober to check the address it actually
connected to, at connect time. Until then a determined customer can still reach
an internal address, and that is a known gap rather than an oversight.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Final
from urllib.parse import urlsplit

from nexovue_api.checks.schemas import MAX_STEPS_PER_FLOW
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


def validate_step_urls(config: dict[str, Any]) -> None:
    """Every URL a browser flow will navigate to, not just the check's target.

    A browser check carries its own step list, and each `goto` step names a URL.
    Validating only `target` left those unchecked — so a check whose target was
    a public site could still walk our prober to the metadata endpoint, with
    `expect_text` reporting back whether a given string was present. That is the
    same request-forgery hole this module exists to close, reached by a second
    door I had not looked at.
    """
    steps = config.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list):
        raise InvalidRequestError("A check's steps must be a list.")
    if len(steps) > MAX_STEPS_PER_FLOW:
        raise InvalidRequestError(f"A flow may have at most {MAX_STEPS_PER_FLOW} steps.")

    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise InvalidRequestError(f"Step {position} is not a step.")
        url = step.get("url")
        if url is None:
            continue
        if not isinstance(url, str):
            raise InvalidRequestError(f"Step {position} has a url that is not text.")
        # Same rules as the target. A step is a request we make on our own
        # infrastructure exactly as the target is.
        validate_target(url)


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
