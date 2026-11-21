"""The phase arithmetic, without a network.

libcurl reports cumulative offsets from the start of the request. Turning those
into per-phase durations is where the breakdown is either right or quietly
wrong, and it can be checked exactly with recorded numbers — no target, no
timing flake.
"""

from __future__ import annotations

from typing import Any, Final

import pycurl
import pytest

from nexovue_scheduler.probes.http_check import (
    HEADER_REFUSAL,
    PreparedRequest,
    _phases,
    _total,
    perform,
)
from nexovue_scheduler.probes.result import PHASE_ORDER

# Real offsets from an HTTPS request to example.com, in libcurl's own form:
# each is seconds since the request began.
HTTPS_OFFSETS: Final = {
    pycurl.NAMELOOKUP_TIME: 0.0493,
    pycurl.CONNECT_TIME: 0.0908,
    pycurl.APPCONNECT_TIME: 0.1475,
    pycurl.STARTTRANSFER_TIME: 0.1968,
    pycurl.TOTAL_TIME: 0.1969,
}

# The same request over plain HTTP. APPCONNECT_TIME stays at zero because no
# handshake happened — it does not fall back to matching CONNECT_TIME.
HTTP_OFFSETS: Final = {
    pycurl.NAMELOOKUP_TIME: 0.0014,
    pycurl.CONNECT_TIME: 0.0386,
    pycurl.APPCONNECT_TIME: 0.0,
    pycurl.STARTTRANSFER_TIME: 0.1486,
    pycurl.TOTAL_TIME: 0.1487,
}

# A rejected certificate. libcurl leaves TOTAL_TIME at zero here while the
# offsets up to the failure are populated.
REJECTED_OFFSETS: Final = {
    pycurl.NAMELOOKUP_TIME: 0.3178,
    pycurl.CONNECT_TIME: 0.5696,
    pycurl.APPCONNECT_TIME: 0.0,
    pycurl.STARTTRANSFER_TIME: 0.0,
    pycurl.TOTAL_TIME: 0.0,
}


class FakeCurl:
    """Answers getinfo from recorded offsets, and nothing else."""

    def __init__(self, offsets: dict[int, float]) -> None:
        self._offsets = offsets

    def getinfo(self, info: int) -> Any:
        return self._offsets[info]


def test_https_phases_sum_to_the_total() -> None:
    """The breakdown accounts for the whole request, to the last microsecond."""
    phases = _phases(FakeCurl(HTTPS_OFFSETS))

    assert set(phases) == set(PHASE_ORDER)
    assert sum(phases.values()) == pytest.approx(HTTPS_OFFSETS[pycurl.TOTAL_TIME], abs=1e-9)
    assert phases["dns"] == pytest.approx(0.0493)
    assert phases["tls"] == pytest.approx(0.1475 - 0.0908)


def test_plain_http_reports_no_handshake_without_losing_the_wait() -> None:
    """The case a naive subtraction gets wrong.

    With no TLS, APPCONNECT_TIME is zero rather than equal to CONNECT_TIME.
    Subtracting it blindly would make the TLS phase hugely negative and the TTFB
    phase absorb the whole request.
    """
    phases = _phases(FakeCurl(HTTP_OFFSETS))

    assert phases["tls"] == 0.0
    assert phases["ttfb"] == pytest.approx(0.1486 - 0.0386)
    assert sum(phases.values()) == pytest.approx(HTTP_OFFSETS[pycurl.TOTAL_TIME], abs=1e-9)


def test_a_phase_that_never_happened_is_zero_rather_than_missing() -> None:
    """Every check reports all five steps, so a chart has no gaps."""
    assert set(_phases(FakeCurl(REJECTED_OFFSETS))) == set(PHASE_ORDER)


def test_a_failure_reports_the_time_it_actually_spent() -> None:
    """TOTAL_TIME is zero on a rejected certificate, but the request took time.

    Reporting zero would say a check that spent half a second failing was
    instant, and would contradict its own phase breakdown.
    """
    curl = FakeCurl(REJECTED_OFFSETS)
    phases = _phases(curl)

    assert _total(curl, phases) == pytest.approx(sum(phases.values()))
    assert _total(curl, phases) > 0.5


def test_a_successful_request_uses_the_measured_total() -> None:
    """The fallback must not displace the real measurement when there is one."""
    curl = FakeCurl(HTTPS_OFFSETS)
    assert _total(curl, _phases(curl)) == pytest.approx(HTTPS_OFFSETS[pycurl.TOTAL_TIME])


# ------------------------------------------------- F3: what `perform` refuses to send


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Bearer sk-1\r\nX-Injected: 1")],
        [("X-Api-Key", "sk-1\n")],
        [("X-Name\r\nInjected", "1")],
        [("Authorization", "Bearer sk\x00")],
    ],
)
def test_a_header_holding_a_control_character_is_never_sent(
    headers: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3 AC34: a line break in a key could end its header and start another. `perform`
    refuses before libcurl is even built, so nothing reaches any address."""

    def no_curl() -> None:
        raise AssertionError("a request with a control character reached libcurl")

    monkeypatch.setattr(pycurl, "Curl", no_curl)
    exchange = perform(PreparedRequest(method="POST", url="https://example.com", headers=headers))

    assert exchange.error == HEADER_REFUSAL
    assert exchange.status_code is None
    assert set(exchange.phases) == set(PHASE_ORDER)
