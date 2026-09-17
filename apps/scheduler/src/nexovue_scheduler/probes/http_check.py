"""One HTTP request: any method, headers, a body, and what came back.

libcurl records a timestamp at each stage of a request and exposes them as
cumulative offsets from the start. Subtracting adjacent ones gives the phase
durations, which is the difference between "the site is slow" and "DNS is slow"
— and the reason this uses pycurl rather than a higher-level client that reports
only a total.

The certificate is read from the same connection rather than by a second probe.
A separate TLS check would open a second connection to learn something the first
one already knew.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import pycurl

from nexovue_scheduler.probes.addresses import ConnectGuard
from nexovue_scheduler.probes.result import (
    PHASE_DNS,
    PHASE_TCP,
    PHASE_TLS,
    PHASE_TRANSFER,
    PHASE_TTFB,
    Certificate,
    HttpExchange,
)

# Identifies us in the customer's access logs. A probe that arrives anonymously
# looks like scraping, and the person reading those logs deserves to know who is
# calling and why.
USER_AGENT: Final = "nexovue-prober/0.1 (+https://nexovue.com/probes)"

# What an assertion can read of a response. Past it the body is cut off and the
# cut recorded: a hostile or misconfigured target decides how much it sends.
MAX_BODY_BYTES: Final = 1 * 1024 * 1024

MAX_REDIRECTS: Final = 5

# libcurl's answer when the write callback declined more bytes — which is us
# stopping at the cap, not the target failing.
_WRITE_DECLINED: Final = pycurl.E_WRITE_ERROR

# Methods libcurl sends natively. Anything else goes through CUSTOMREQUEST, which
# libcurl keeps across redirects; POST is left to libcurl so that a 303 becomes
# the GET every client makes of it.
_GET: Final = "GET"
_HEAD: Final = "HEAD"
_POST: Final = "POST"


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """A request with every reference substituted, ready to send."""

    method: str
    url: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | None = None
    follow_redirects: bool = True
    timeout_seconds: int = 10


class _ResponseReader:
    """Collects the final response's headers and a capped body."""

    def __init__(self) -> None:
        self.headers: list[tuple[str, str]] = []
        self.chunks: list[bytes] = []
        self.received = 0
        self.truncated = False

    def header_line(self, line: bytes) -> None:
        text = line.decode("iso-8859-1").rstrip("\r\n")
        if text.startswith("HTTP/"):
            # A new response in a redirect chain: only the last one's headers count.
            self.headers = []
        elif ":" in text:
            name, _, value = text.partition(":")
            self.headers.append((name.strip(), value.strip()))

    def body_chunk(self, chunk: bytes) -> int | None:
        room = MAX_BODY_BYTES - self.received
        if len(chunk) > room:
            self.chunks.append(chunk[:room])
            self.received += room
            self.truncated = True
            return 0  # declines the rest; libcurl stops with a write error
        self.chunks.append(chunk)
        self.received += len(chunk)
        return None


def perform(request: PreparedRequest, *, private_allowed: bool = False) -> HttpExchange:
    """Send one request and report what happened, success or failure.

    Never raises for a target's behaviour — an unreachable host is a result, not
    an error. It raises only if pycurl itself cannot be configured, which is a
    bug here rather than news about the target.
    """
    reader = _ResponseReader()
    curl = pycurl.Curl()
    guard = ConnectGuard(private_allowed=private_allowed)
    try:
        _configure(curl, request, reader, guard)
        error: str | None = None
        try:
            curl.perform()
        except pycurl.error as failure:
            code = failure.args[0] if failure.args else None
            if not (code == _WRITE_DECLINED and reader.truncated):
                # When we are the ones who refused, libcurl reports a plain "could
                # not connect", which would read as the target being down. Our own
                # reason replaces it so a rebinding attempt is visible as one.
                error = guard.refusal_reason() or str(failure.args[-1] if failure.args else failure)

        phases = _phases(curl)
        status = int(curl.getinfo(pycurl.RESPONSE_CODE)) or None
        return HttpExchange(
            status_code=status if error is None else None,
            headers=reader.headers if error is None else [],
            body=b"".join(reader.chunks) if error is None else b"",
            body_truncated=reader.truncated,
            total_seconds=_total(curl, phases),
            phases=phases,
            certificate=_certificate(curl, request.url) if error is None else None,
            effective_url=str(curl.getinfo(pycurl.EFFECTIVE_URL) or request.url),
            error=error,
        )
    finally:
        curl.close()


def _configure(
    curl: Any, request: PreparedRequest, reader: _ResponseReader, guard: ConnectGuard
) -> None:
    curl.setopt(pycurl.URL, request.url)
    # The only place the address is fixed and cannot be re-resolved behind us.
    # Redirects are judged hop by hop here, not just the first.
    curl.setopt(pycurl.OPENSOCKETFUNCTION, guard.open_socket)
    curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
    curl.setopt(pycurl.REDIR_PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
    curl.setopt(pycurl.USERAGENT, USER_AGENT)
    curl.setopt(pycurl.TIMEOUT, request.timeout_seconds)
    curl.setopt(pycurl.FOLLOWLOCATION, request.follow_redirects)
    curl.setopt(pycurl.MAXREDIRS, MAX_REDIRECTS)
    curl.setopt(pycurl.HEADERFUNCTION, reader.header_line)
    curl.setopt(pycurl.WRITEFUNCTION, reader.body_chunk)
    # Certificate details are only populated when curl is asked for them.
    curl.setopt(pycurl.OPT_CERTINFO, 1)
    # A stale connection reused between checks would report near-zero DNS, TCP
    # and TLS and quietly make the breakdown meaningless. Every check measures a
    # full connection, which is what the customer's own visitors experience.
    curl.setopt(pycurl.FRESH_CONNECT, True)
    curl.setopt(pycurl.FORBID_REUSE, True)

    # `Expect:` stops libcurl pausing a large body for a 100-continue the target
    # may never send, which would show as a second of our own making in `ttfb`.
    headers = [_header_line(name, value) for name, value in request.headers] + ["Expect:"]
    curl.setopt(pycurl.HTTPHEADER, headers)
    _configure_method_and_body(curl, request)


def _configure_method_and_body(curl: Any, request: PreparedRequest) -> None:
    method = request.method
    if request.body is not None:
        curl.setopt(pycurl.POSTFIELDS, request.body)
        curl.setopt(pycurl.POSTFIELDSIZE_LARGE, len(request.body))

    has_body = request.body is not None
    if method == _HEAD:
        curl.setopt(pycurl.NOBODY, True)
    elif method == _POST and has_body:
        return  # libcurl's own POST, redirect semantics included
    elif method == _GET and not has_body:
        return
    else:
        curl.setopt(pycurl.CUSTOMREQUEST, method)


def _header_line(name: str, value: str) -> str:
    # `Name;` is libcurl's syntax for a header sent with an empty value; `Name:`
    # would remove it instead.
    return f"{name}: {value}" if value else f"{name};"


def _phases(curl: Any) -> dict[str, float]:
    """Turn libcurl's cumulative offsets into per-phase durations.

    Every value is a time since the request started, so each phase is the gap
    between two of them, and for a clean single-connection request they sum
    exactly to the total.

    **They are not guaranteed to.** Measured over several hundred live probes,
    two disagreed — both slow outliers, phases exceeding the total by 3-4%.
    libcurl's offsets stop composing when it retries internally, and a slice of
    time then falls inside two phases. The total stays the accurate number; the
    breakdown is the approximation, so a reader comparing them should divide by
    the phase sum rather than by the total.

    A phase that never happened reports zero rather than being omitted: a plain
    HTTP request has no TLS handshake, and a missing series would break a chart
    where a flat zero reads correctly.
    """
    dns = curl.getinfo(pycurl.NAMELOOKUP_TIME)
    connected = curl.getinfo(pycurl.CONNECT_TIME)
    secured = curl.getinfo(pycurl.APPCONNECT_TIME)
    first_byte = curl.getinfo(pycurl.STARTTRANSFER_TIME)
    total = curl.getinfo(pycurl.TOTAL_TIME)

    # Without TLS, APPCONNECT_TIME stays 0 rather than matching CONNECT_TIME, so
    # the TTFB phase has to measure from the connection instead.
    handshake_end = secured if secured > 0 else connected

    return {
        PHASE_DNS: _gap(0.0, dns),
        PHASE_TCP: _gap(dns, connected),
        PHASE_TLS: _gap(connected, secured) if secured > 0 else 0.0,
        PHASE_TTFB: _gap(handshake_end, first_byte),
        PHASE_TRANSFER: _gap(first_byte, total),
    }


def _total(curl: Any, phases: dict[str, float]) -> float:
    """How long the request took, staying consistent with the phases.

    libcurl leaves TOTAL_TIME at zero on some failures — a rejected certificate
    is one — while the phase offsets up to that point are populated. Reporting
    zero there would say a check that spent half a second failing took no time,
    and would contradict its own breakdown. The phases are the fallback because
    they are the thing that actually happened.
    """
    measured = curl.getinfo(pycurl.TOTAL_TIME)
    return measured if measured > 0 else sum(phases.values())


def _gap(start: float, end: float) -> float:
    """A phase duration, never negative.

    libcurl's offsets are monotonic in practice, but a failed request leaves
    later ones at zero, and a negative duration would poison a sum.
    """
    return max(end - start, 0.0)


def _certificate(curl: Any, target: str) -> Certificate | None:
    """Days until the served certificate expires, when the connection was TLS.

    Returns None rather than raising for a plain-HTTP target or a certificate
    curl described in a shape we do not recognise. Losing the expiry reading is
    not a reason to lose the uptime reading it arrived with.
    """
    chain = curl.getinfo(pycurl.INFO_CERTINFO)
    if not chain:
        return None

    # The leaf is first; the rest of the chain is the issuers above it. pycurl
    # hands each certificate over as (field, value) pairs already parsed.
    fields = dict(chain[0])
    expires_at = _parse_expiry(fields.get("Expire date", ""))
    if expires_at is None:
        return None

    return Certificate(
        days_remaining=(expires_at - datetime.now(UTC)).days,
        issuer=fields.get("Issuer", ""),
        server_address=_host_of(target),
    )


def _parse_expiry(value: str) -> datetime | None:
    """Read curl's certificate date, which is GMT in a fixed format."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        return None


def _host_of(target: str) -> str:
    return urlsplit(target).hostname or target
