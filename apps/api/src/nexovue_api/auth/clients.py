"""What a session is recognised by, and the deliberate decision to keep it coarse.

A person looking at their own session list is answering one question: *is one of
these not me?* Everything stored here exists to answer that question and nothing
else, because of who else can read the answer.

**The reader of this list may be the attacker.** The list is shown to whoever
holds a session, which is precisely the situation the feature exists for —
somebody suspects another person is signed in as them. If that other person is,
they open the same screen. So every field here is published to the intruder as
well as to the owner, and the question for each one is not "is this useful?" but
"is this useful enough to be worth handing over?"

**The User-Agent is reduced to a family and an operating system, at the moment it
arrives, and the raw string is never written anywhere.** A full User-Agent
carries browser build numbers, device models and vendor extensions — enough to
identify a device across unrelated sites, and enough for an intruder to learn
what hardware their target uses. "Chrome on macOS" answers the owner's question
just as well. Reducing on the way in rather than on the way out matters: what is
never stored cannot leak from the store, and cannot be quietly exposed later by a
screen that decides to show a little more.

**The address is truncated to its network — /24 for IPv4, /48 for IPv6 — and only
the network is kept.** A precise address is a household or an office; published
to an intruder it discloses roughly where its owner is, which is a physical-safety
matter and not a debugging convenience. The truncated network still separates
"my usual connection" from "somewhere else entirely", which is the whole of what
recognition needs. Abuse investigation is a different job with a different
retention regime and belongs to the request log, not to a list a customer reads.

**The matching below is deliberately shallow, and a parser library was rejected
for that reason.** A full User-Agent parser's value is precision — exact versions,
device names, engine builds — which is exactly what must not be stored. A table of
a dozen families cannot drift into recording more than was decided here.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final

from starlette.datastructures import Headers

USER_AGENT_HEADER: Final = "user-agent"
FORWARDED_FOR_HEADER: Final = "x-forwarded-for"

# Beyond this, a User-Agent is not a browser being honest. Truncated before
# matching so no caller can make us scan a megabyte per sign-in.
MAX_USER_AGENT_BYTES: Final = 512

# The bits of an address that are kept. A /24 is the smallest routable IPv4
# block; a /48 is what a site is typically delegated in IPv6, so the two carry
# comparable meaning rather than comparable length.
IPV4_NETWORK_BITS: Final = 24
IPV6_NETWORK_BITS: Final = 48

# Ordered, because the tokens nest: every Edge and Opera User-Agent also says
# "Chrome", and every Chrome one also says "Safari". First match wins.
_BROWSER_TOKENS: Final = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
    ("curl/", "curl"),
    ("HTTPie", "HTTPie"),
    ("python-requests", "a Python client"),
    ("PostmanRuntime", "Postman"),
)

# Also ordered: an iPhone User-Agent says "like Mac OS X", and Android says
# "Linux". The more specific platform has to be tested first.
_PLATFORM_TOKENS: Final = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("CrOS", "ChromeOS"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


@dataclass(frozen=True, slots=True)
class ClientDescription:
    """How a session is told apart from another. Both halves may be absent.

    Absent rather than guessed: "Unknown device" is an honest answer a customer
    can act on, and an invented one is not.
    """

    label: str | None
    network: str | None

    @classmethod
    def unknown(cls) -> ClientDescription:
        return cls(label=None, network=None)


def describe_client(headers: Headers, client_ip: str | None) -> ClientDescription:
    """Reduce a request to the two things a session list may show."""
    return ClientDescription(
        label=client_label(headers.get(USER_AGENT_HEADER)),
        network=network_of(client_ip),
    )


def client_ip_of(
    headers: Headers, *, direct_peer: str | None, trusted_proxy_count: int
) -> str | None:
    """The caller's address, trusting `X-Forwarded-For` only as far as configured.

    With no trusted proxies the direct peer is the only honest answer: a
    forwarded header from an untrusted client is just a value it chose, and
    believing it would let anybody write whatever network they liked into their
    own session list.

    **`middleware/ratelimit.py` calls this**, rather than holding the second
    copy it held until 2026-09-14. It still reads the proxy count for itself,
    because middleware runs before any dependency exists to hand it one — but
    the rule about what to believe lives here alone. Two copies of a trust
    boundary is one too many: whichever drifts first does so silently, and the
    two failures are a rate limiter counting the proxy instead of the caller,
    and a session list showing somebody a network that is not theirs.
    """
    if trusted_proxy_count > 0:
        forwarded = headers.get(FORWARDED_FOR_HEADER, "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if len(hops) >= trusted_proxy_count:
            return hops[-trusted_proxy_count]
    return direct_peer


def client_label(user_agent: str | None) -> str | None:
    """ "Chrome on macOS", "curl", or None when nothing is recognised."""
    if not user_agent:
        return None
    candidate = user_agent[:MAX_USER_AGENT_BYTES]

    browser = _first_match(candidate, _BROWSER_TOKENS)
    platform = _first_match(candidate, _PLATFORM_TOKENS)
    if browser is None:
        return platform
    return f"{browser} on {platform}" if platform is not None else browser


def network_of(client_ip: str | None) -> str | None:
    """The address's network in CIDR form, or None when it is not an address.

    An unparseable value is not recorded at all. It would have come from a
    proxy header we already decided how far to trust, and storing whatever
    arrived would make this field a place a caller can write into.
    """
    if not client_ip:
        return None
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return None
    bits = IPV4_NETWORK_BITS if address.version == 4 else IPV6_NETWORK_BITS
    return str(ipaddress.ip_network(f"{address}/{bits}", strict=False))


def _first_match(candidate: str, tokens: tuple[tuple[str, str], ...]) -> str | None:
    return next((name for token, name in tokens if token in candidate), None)
