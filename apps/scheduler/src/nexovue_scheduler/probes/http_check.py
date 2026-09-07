"""The HTTP check: is it up, how long did it take, and which part was slow.

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

import io
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
    ProbeResult,
)

CHECK_TYPE_HTTP: Final = "http"

# Identifies us in the customer's access logs. A probe that arrives anonymously
# looks like scraping, and the person reading those logs deserves to know who is
# calling and why.
USER_AGENT: Final = "nexovue-prober/0.1 (+https://nexovue.com/probes)"

# A response body we never read still has to be downloaded to time the transfer
# phase. This bounds what a hostile or misconfigured target can make us hold.
MAX_BODY_BYTES: Final = 1 * 1024 * 1024

_HTTP_ERROR_FLOOR: Final = 400


def perform(
    check_id: str, target: str, timeout_seconds: int, *, private_allowed: bool = False
) -> ProbeResult:
    """Run one HTTP check and report what happened, success or failure.

    Never raises for a target's behaviour — an unreachable host is a result, not
    an error. It raises only if pycurl itself cannot be configured, which is a
    bug here rather than news about the target.
    """
    body = io.BytesIO()
    curl = pycurl.Curl()
    guard = ConnectGuard(private_allowed=private_allowed)
    try:
        _configure(curl, target, timeout_seconds, body, guard)
        try:
            curl.perform()
        except pycurl.error as failure:
            # Down, refused, timed out, TLS rejected. The timings up to the
            # point of failure are still true and are still published: knowing
            # DNS resolved before the connection was refused is the diagnosis.
            #
            # When we are the ones who refused, libcurl reports a plain "could
            # not connect", which would read as the target being down. Our own
            # reason replaces it so a rebinding attempt is visible as one.
            phases = _phases(curl)
            reason = guard.refusal_reason() or str(failure)
            return ProbeResult(
                check_id=check_id,
                check_type=CHECK_TYPE_HTTP,
                target=target,
                up=False,
                status_code=None,
                total_seconds=_total(curl, phases),
                phases=phases,
                error=reason,
            )

        status = int(curl.getinfo(pycurl.RESPONSE_CODE))
        phases = _phases(curl)
        return ProbeResult(
            check_id=check_id,
            check_type=CHECK_TYPE_HTTP,
            target=target,
            up=status < _HTTP_ERROR_FLOOR,
            status_code=status,
            total_seconds=_total(curl, phases),
            phases=phases,
            certificate=_certificate(curl, target),
        )
    finally:
        curl.close()


def _configure(
    curl: Any, target: str, timeout_seconds: int, body: io.BytesIO, guard: ConnectGuard
) -> None:
    curl.setopt(pycurl.URL, target)
    # The only place the address is fixed and cannot be re-resolved behind us.
    # Redirects are followed below, so this judges every hop, not just the first.
    curl.setopt(pycurl.OPENSOCKETFUNCTION, guard.open_socket)
    curl.setopt(pycurl.USERAGENT, USER_AGENT)
    curl.setopt(pycurl.TIMEOUT, timeout_seconds)
    # Redirects are followed because a customer checking a site expects the
    # answer the browser would get, and the timings cover the whole chain.
    curl.setopt(pycurl.FOLLOWLOCATION, True)
    curl.setopt(pycurl.MAXREDIRS, 5)
    curl.setopt(pycurl.WRITEDATA, body)
    curl.setopt(pycurl.MAXFILESIZE, MAX_BODY_BYTES)
    # Certificate details are only populated when curl is asked for them.
    curl.setopt(pycurl.OPT_CERTINFO, 1)
    # A stale connection reused between checks would report near-zero DNS, TCP
    # and TLS and quietly make the breakdown meaningless. Every check measures a
    # full connection, which is what the customer's own visitors experience.
    curl.setopt(pycurl.FRESH_CONNECT, True)
    curl.setopt(pycurl.FORBID_REUSE, True)


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
    """How long the check took, staying consistent with the phases.

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
