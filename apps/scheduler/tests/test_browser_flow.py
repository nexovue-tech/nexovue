"""Driving a flow behind the egress boundary, without a browser.

What the probe decides *around* Chromium — the flags it launches with, that a
step the proxy refused during is a failed step, that a flow does not start
without the proxy, that a step may only open http or https — is asserted here
against fakes. The live suite then only has to prove Chromium does what these
tests assume.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from loopback_site import LoopbackSite
from proxy_client import refuse_loopback_through

from nexovue_scheduler.probes import browser
from nexovue_scheduler.probes.browser import (
    ERROR_TYPE_CONFIGURATION,
    BrowserStep,
    EgressRefusedError,
    FlowConfigError,
    launch_options,
    parse_steps,
)
from nexovue_scheduler.probes.browser_proxy import PublicAddressProxy

TARGET = "https://shop.acme.example/checkout"
PROXY_ENDPOINT = "http://127.0.0.1:4444"


class RecordedJudge:
    """A proxy that refused what it was told to, one step at a time."""

    def __init__(self, *per_step: list[str]) -> None:
        self._per_step = list(per_step)

    def take_refusals(self) -> list[str]:
        return self._per_step.pop(0) if self._per_step else []


class FakePage:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.opened: list[str] = []
        self.url: str | None = None
        self.timeout_ms: int | None = None

    def set_default_timeout(self, milliseconds: int) -> None:
        self.timeout_ms = milliseconds

    def goto(self, url: str) -> None:
        self.opened.append(url)
        self.url = url
        if self.failure is not None:
            raise self.failure


class FakeBrowser:
    """Chromium's place in the wiring, so the options it was handed can be read."""

    version = "fake-chromium/0"

    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self._page

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, page: FakePage, watch: Callable[[dict[str, Any]], None]) -> None:
        self._page = page
        self._watch = watch

    def launch(self, **options: Any) -> FakeBrowser:
        self._watch(options)
        return FakeBrowser(self._page)


@contextmanager
def _fake_playwright(
    monkeypatch: pytest.MonkeyPatch, page: FakePage, watch: Callable[[dict[str, Any]], None]
) -> Iterator[None]:
    """Stand in for `playwright.sync_api`, which `_drive` imports when it runs.

    Chromium's own behaviour is the live suite's to prove. What is asserted
    through this is the wiring: which options the probe hands the browser.
    """

    class FakePlaywright:
        chromium = None

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    module = types.ModuleType("playwright.sync_api")

    def sync_playwright() -> FakePlaywright:
        started = FakePlaywright()
        started.chromium = FakeChromium(page, watch)  # type: ignore[assignment]
        return started

    module.sync_playwright = sync_playwright  # type: ignore[attr-defined]
    module.Error = _FakePlaywrightError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    yield


class _FakePlaywrightError(Exception):
    """What `playwright.sync_api.Error` is, for the offline suite."""


GOTO = BrowserStep(name="open", action="goto", url=TARGET)


# ------------------------------------------------------------- how it launches


def test_chromium_is_launched_through_the_proxy_with_no_other_way_out() -> None:
    options = launch_options(PROXY_ENDPOINT)

    assert options["proxy"] == {"server": PROXY_ENDPOINT, "bypass": "<-loopback>"}
    resolver_rules = next(a for a in options["args"] if a.startswith("--host-resolver-rules="))
    assert "MAP * ~NOTFOUND" in resolver_rules
    assert "EXCLUDE 127.0.0.1" in resolver_rules


def test_both_spellings_of_the_webrtc_switch_are_passed() -> None:
    """WebRTC sends UDP no HTTP proxy can carry, and Chromium ignores a switch
    it does not know. The two builds Playwright picks from spell this one
    differently, so which binary launched must not decide whether it applies."""
    options = launch_options(PROXY_ENDPOINT)

    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in options["args"]
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in options["args"]


def test_the_browser_is_pinned_headless_so_it_is_the_build_that_was_tested() -> None:
    """Playwright launches `chromium-headless-shell` when headless and Chrome
    for Testing when not. They are different binaries; only one is measured."""
    assert launch_options(PROXY_ENDPOINT)["headless"] is True


def test_the_options_reach_the_browser_and_name_the_proxy_that_judges_the_flow(
    monkeypatch: pytest.MonkeyPatch, loopback_site: LoopbackSite
) -> None:
    """The boundary is only a boundary if `_drive` hands it to the browser.

    Asserting what `launch_options()` returns would stay green with the call
    deleted, so this reads what `chromium.launch` was actually given, and then
    speaks to that address itself — a well-formed endpoint string is not a
    proxy. The refusal that probe earns then fails the flow, which ties the
    address Chromium was handed to the verdict the steps are judged by.
    """
    handed: dict[str, Any] = {}
    verdicts: list[str] = []

    def watch(options: dict[str, Any]) -> None:
        handed.update(options)
        verdicts.append(refuse_loopback_through(options["proxy"]["server"], loopback_site.port))

    with _fake_playwright(monkeypatch, FakePage(), watch):
        flow = browser.perform("bound", TARGET, 5)

    assert handed, "the browser was launched with no options at all"
    assert handed == launch_options(handed["proxy"]["server"])
    assert verdicts == ["HTTP/1.1 403 Forbidden"]
    assert loopback_site.hits == []
    assert flow.failure is not None
    assert "127.0.0.1" in flow.failure.message


def test_a_flow_does_not_start_without_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed. A browser launched without its boundary is the defect itself."""

    def cannot_listen(self: PublicAddressProxy) -> None:
        raise OSError("cannot listen")

    monkeypatch.setattr(PublicAddressProxy, "start", cannot_listen)

    with pytest.raises(OSError, match="cannot listen"):
        browser.perform("flow", TARGET, 5)


# ------------------------------------------------------------- judging a step


def test_a_refusal_during_a_step_fails_it_whatever_the_page_showed() -> None:
    """A plain-HTTP refusal renders as a page; the proxy's word is what counts."""
    page = FakePage()

    with pytest.raises(EgressRefusedError) as refused:
        browser._run_judged_step(page, GOTO, TARGET, RecordedJudge(["127.0.0.1"]))

    assert page.opened == [TARGET]
    assert "not contacted" in str(refused.value)
    assert "127.0.0.1" in str(refused.value)


def test_a_refusal_is_the_reason_when_chromium_also_failed() -> None:
    """ERR_TUNNEL_CONNECTION_FAILED reads as the site being down. It was not."""
    chromium_said = RuntimeError("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED")

    with pytest.raises(EgressRefusedError) as refused:
        browser._run_judged_step(FakePage(chromium_said), GOTO, TARGET, RecordedJudge(["10.0.0.5"]))

    assert "10.0.0.5" in str(refused.value)
    assert refused.value.__cause__ is chromium_said


def test_an_ordinary_failure_keeps_its_own_message() -> None:
    """Nothing refused, so nothing of ours replaces what actually went wrong."""
    with pytest.raises(AssertionError, match="expected 'Thanks'"):
        browser._run_judged_step(
            FakePage(AssertionError("expected 'Thanks' in 'h1'")), GOTO, TARGET, RecordedJudge()
        )


def test_a_step_the_proxy_refused_nothing_for_passes() -> None:
    browser._run_judged_step(FakePage(), GOTO, TARGET, RecordedJudge([]))


def test_refusals_are_attributed_to_the_step_they_happened_in() -> None:
    judge = RecordedJudge([], ["169.254.169.254"])
    browser._run_judged_step(FakePage(), GOTO, TARGET, judge)

    with pytest.raises(EgressRefusedError) as refused:
        browser._run_judged_step(FakePage(), GOTO, TARGET, judge)

    assert "169.254.169.254" in str(refused.value)


def test_a_refusal_the_last_step_caused_still_fails_the_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chromium loads subresources asynchronously, so a refusal can land after
    the step that caused it has returned. With nothing draining after the final
    step, a flow whose last step was blocked reported `up`."""
    with _fake_playwright(monkeypatch, FakePage(), lambda _options: None):
        passed, failure = browser._run_steps(
            FakePage(), [GOTO], TARGET, RecordedJudge([], ["10.0.0.5"]), "fake-chromium/0"
        )

    assert failure is not None, "a refusal after the last step went unreported"
    assert failure.index == 0
    assert failure.error_type == "EgressRefusedError"
    assert "10.0.0.5" in failure.message
    assert passed == 0


# ------------------------------------------------------ what a step may open


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "chrome://version",
        "javascript:alert(1)",
        "data:text/html,<h1>x</h1>",
        "ftp://example.com/",
        "shop.acme.example/checkout",  # no scheme at all
    ],
)
def test_a_step_may_only_open_http_or_https(url: str) -> None:
    """A proxy judges network requests; these never make one."""
    with pytest.raises(FlowConfigError) as rejected:
        parse_steps(
            {"steps": [{"action": "goto", "url": TARGET}, {"action": "goto", "url": url}]}, TARGET
        )

    assert rejected.value.index == 1
    assert "http or https" in rejected.value.message


def test_the_default_step_holds_the_target_to_the_same_rule() -> None:
    flow = browser.perform("flow", "file:///etc/passwd", 5)

    assert flow.up is False
    assert flow.failure is not None
    assert flow.failure.error_type == ERROR_TYPE_CONFIGURATION
    assert "http or https" in flow.failure.message


def test_an_http_step_is_still_a_step() -> None:
    steps = parse_steps({"steps": [{"action": "goto", "url": "http://shop.acme.example/"}]}, TARGET)

    assert steps[0].url == "http://shop.acme.example/"
