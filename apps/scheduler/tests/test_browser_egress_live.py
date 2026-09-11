"""The browser's egress boundary, against a real Chromium and real DNS.

`127.0.0.1.nip.io` is a public name that answers 127.0.0.1. The API accepts it
at creation — it is a name, not an address — and before the proxy existed a
browser check pointed at it opened a page served on loopback *inside the
worker* and `expect_text` reported back what it found. Both arms of that
control were observed: the loopback service recording a hit and handing over
its secret without the boundary, and no hit at all with it.

Needs Playwright with its Chromium, and DNS. It fails rather than skips when
either is missing, for the reason `live.py` gives: a boundary test that quietly
did not run looks exactly like one that held.
"""

from __future__ import annotations

import socket
import threading
from typing import Final

import pytest
from live import require_stack
from loopback_site import LoopbackSite

from nexovue_scheduler.probes import browser
from nexovue_scheduler.probes.browser_proxy import THREAD_NAME, PublicAddressProxy

# Names that resolve to a fixed address by design. Real DNS, not a mock of it.
LOOPBACK_NAME: Final = "127.0.0.1.nip.io"
METADATA_NAME: Final = "169.254.169.254.nip.io"

PUBLIC_SITE: Final = "https://example.com/"
# Answers 302 to wherever `url` says. Chromium follows it inside one navigation,
# which is the hop a check of the step's own URL would never see.
REDIRECTOR: Final = "https://httpbin.org/redirect-to"

STEP_TIMEOUT_SECONDS: Final = 15

# Playwright adds `<-loopback>` to the bypass list itself whenever ours does not
# already name loopback, so a Chromium launched without `PROXY_BYPASS_NOTHING`
# still proxies loopback and nothing leaks. That second copy is what left the
# line with no behavioural guard. This is Playwright's own switch for turning it
# off, and with it off the boundary rests on our line alone — which is the only
# state in which deleting our line can be observed.
PLAYWRIGHT_LOOPBACK_BYPASS_OFF: Final = "PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK"

# Offers ICE and reports every candidate the browser gathered. A UDP candidate
# is a socket opened straight to an address the page named, which no HTTP proxy
# carries; with `disable_non_proxied_udp` Chromium gathers none at all.
GATHER_ICE_CANDIDATES: Final = """
(timeoutMilliseconds) => new Promise((resolve) => {
  const connection = new RTCPeerConnection();
  const gathered = [];
  const finish = () => { connection.close(); resolve(gathered); };
  connection.onicecandidate = (event) => {
    if (event.candidate && event.candidate.candidate) gathered.push(event.candidate.candidate);
    else finish();
  };
  connection.createDataChannel("egress-probe");
  connection.createOffer().then((offer) => connection.setLocalDescription(offer));
  setTimeout(finish, timeoutMilliseconds);
})
"""
ICE_GATHERING_TIMEOUT_MILLISECONDS: Final = 5000
BLANK_PAGE: Final = "about:blank"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as playwright:
        try:
            playwright.chromium.launch().close()
        except Exception:
            return False
    return True


def _dns_works() -> bool:
    try:
        socket.getaddrinfo(LOOPBACK_NAME, 80)
    except OSError:
        return False
    return True


require_stack(
    reachable=_chromium_available() and _dns_works(),
    suite="the browser egress boundary's live suite",
    remedy=(
        "Playwright and its Chromium must be installed "
        "(uv sync --all-packages --extra browser; uv run playwright install chromium) "
        f"and {LOOPBACK_NAME} must resolve, which needs DNS and internet access"
    ),
)


def _read_body_step(site: LoopbackSite) -> dict[str, str]:
    return {"action": "expect_text", "selector": "body", "text": site.secret}


def _proxy_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == THREAD_NAME]


# ----------------------------------------------------------------- the boundary


def test_a_public_site_loads_and_the_proxy_stops_with_the_flow() -> None:
    flow = browser.perform("public", PUBLIC_SITE, STEP_TIMEOUT_SECONDS)

    assert flow.up is True, flow.failure
    assert flow.steps_passed == 1
    assert _proxy_threads() == []


def test_a_public_name_answering_loopback_is_never_contacted(loopback_site: LoopbackSite) -> None:
    """The rebinding case end to end. The check must fail, and say why."""
    url = loopback_site.url(LOOPBACK_NAME)

    flow = browser.perform(
        "rebind",
        url,
        STEP_TIMEOUT_SECONDS,
        {"steps": [{"action": "goto", "url": url}, _read_body_step(loopback_site)]},
    )

    assert loopback_site.hits == [], "the browser reached a loopback service"
    assert flow.up is False
    assert flow.failure is not None
    assert flow.failure.index == 0
    assert flow.failure.error_type == "EgressRefusedError"
    assert "not contacted" in flow.failure.message
    assert "127.0.0.1" in flow.failure.message


def test_a_tunnel_refusal_reads_as_a_refusal_and_not_as_a_dead_site(
    loopback_site: LoopbackSite,
) -> None:
    """The other scheme, which takes the other branch.

    Over plain HTTP the refusal arrives as a 403 *page* and `goto` succeeds, so
    the proxy's word is what fails the step. Over HTTPS the browser asks for a
    tunnel, the proxy resolves and judges before opening it, and Chromium
    raises `ERR_TUNNEL_CONNECTION_FAILED` — which reads as the site being down.
    The refusal has to replace that message, or the one result worth
    investigating looks like the most ordinary one.
    """
    url = f"https://{LOOPBACK_NAME}:{loopback_site.port}/"

    flow = browser.perform("tunnel", url, STEP_TIMEOUT_SECONDS)

    assert loopback_site.hits == []
    assert flow.up is False
    assert flow.failure is not None
    assert flow.failure.error_type == "EgressRefusedError"
    assert "not contacted" in flow.failure.message
    assert "127.0.0.1" in flow.failure.message
    assert "ERR_TUNNEL_CONNECTION_FAILED" not in flow.failure.message


def test_a_goto_step_is_judged_exactly_as_the_target_is(loopback_site: LoopbackSite) -> None:
    """A public target with a private step is the same hole through a second door."""
    flow = browser.perform(
        "step",
        PUBLIC_SITE,
        STEP_TIMEOUT_SECONDS,
        {
            "steps": [
                {"action": "goto", "url": PUBLIC_SITE},
                {"action": "goto", "url": loopback_site.url(LOOPBACK_NAME)},
                _read_body_step(loopback_site),
            ]
        },
    )

    assert loopback_site.hits == []
    assert flow.steps_passed == 1
    assert flow.failure is not None
    assert flow.failure.index == 1
    assert "not contacted" in flow.failure.message


def test_a_redirect_to_a_private_address_is_refused(loopback_site: LoopbackSite) -> None:
    """Chromium follows the redirect inside one navigation; every hop is judged."""
    target = f"{REDIRECTOR}?url={loopback_site.url(LOOPBACK_NAME)}&status_code=302"

    flow = browser.perform(
        "redirect",
        target,
        STEP_TIMEOUT_SECONDS,
        {"steps": [{"action": "goto", "url": target}, _read_body_step(loopback_site)]},
    )

    assert loopback_site.hits == []
    assert flow.up is False
    assert flow.failure is not None
    assert flow.failure.index == 0
    assert "not contacted" in flow.failure.message


def test_the_metadata_name_is_refused_by_address() -> None:
    """169.254.169.254 is where every major cloud serves instance credentials."""
    flow = browser.perform("metadata", f"http://{METADATA_NAME}/", STEP_TIMEOUT_SECONDS)

    assert flow.up is False
    assert flow.failure is not None
    assert "169.254.169.254" in flow.failure.message
    assert "not contacted" in flow.failure.message


# --------------------------------------------- the two switches, watched failing


def test_a_loopback_address_is_still_proxied_when_only_our_bypass_list_says_so(
    loopback_site: LoopbackSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`browser.PROXY_BYPASS_NOTHING`, observed rather than spelled out.

    Chromium sends `127.0.0.1` direct, past any proxy, unless the bypass list
    forbids it. Every other test here targets a *name* that resolves to
    loopback, which Chromium hands to the proxy regardless — so none of them
    moves when the line is deleted. This targets the literal address, with
    Playwright's duplicate of the same switch turned off, which is the one
    arrangement where the line is load-bearing on its own.

    Deleting it here: the site records the hit and the flow reads its secret.
    """
    monkeypatch.setenv(PLAYWRIGHT_LOOPBACK_BYPASS_OFF, "1")
    url = loopback_site.url()

    flow = browser.perform(
        "bypass",
        url,
        STEP_TIMEOUT_SECONDS,
        {"steps": [{"action": "goto", "url": url}, _read_body_step(loopback_site)]},
    )

    assert loopback_site.hits == [], "the browser went straight to loopback, around the proxy"
    assert flow.up is False
    assert flow.failure is not None
    assert flow.failure.index == 0
    assert flow.failure.error_type == "EgressRefusedError"
    assert "127.0.0.1" in flow.failure.message


def test_webrtc_gathers_no_candidate_the_proxy_could_not_carry() -> None:
    """The WebRTC switches, observed rather than spelled out.

    WebRTC opens UDP sockets to addresses a page names. An HTTP proxy carries
    none of that, so a page loaded through the boundary can still reach the
    worker's own network over ICE — the leak the switches close.
    No STUN server and no network are needed to see it: with the switches gone
    Chromium gathers host candidates by itself, and each one is a live UDP
    socket outside the proxy.

    `test_both_spellings_of_the_webrtc_switch_are_passed` covers the second
    spelling, which this binary ignores; only the effective one moves here.
    """
    from playwright.sync_api import sync_playwright

    with PublicAddressProxy(private_allowed=False) as proxy, sync_playwright() as playwright:
        chromium = playwright.chromium.launch(**browser.launch_options(proxy.endpoint))
        try:
            page = chromium.new_page()
            page.goto(BLANK_PAGE)
            candidates = page.evaluate(GATHER_ICE_CANDIDATES, ICE_GATHERING_TIMEOUT_MILLISECONDS)
        finally:
            chromium.close()

    assert candidates == [], (
        f"WebRTC gathered {len(candidates)} candidate(s) the proxy never saw: {candidates}"
    )


# ------------------------------------------------------------------ fail closed


def test_a_dead_proxy_fails_the_navigation_and_never_goes_direct(
    loopback_site: LoopbackSite,
) -> None:
    """Chromium, launched as the probe launches it, with nothing listening at
    the proxy's address: the only acceptable outcome is an error."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        dead_port = reserved.getsockname()[1]

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(
            **browser.launch_options(f"http://127.0.0.1:{dead_port}")
        )
        page = chromium.new_page()
        page.set_default_timeout(STEP_TIMEOUT_SECONDS * browser.MILLISECONDS_PER_SECOND)
        with pytest.raises(PlaywrightError) as stopped:
            page.goto(loopback_site.url())
        chromium.close()

    assert "ERR_PROXY_CONNECTION_FAILED" in str(stopped.value)
    assert loopback_site.hits == []
