"""The browser check: drive a real Chromium through a customer's flow.

A browser flow is a narrative, not a number — which step failed, what the page
said, what it looked like — so this door publishes OTLP **logs** rather than the
metrics an HTTP check produces. `packages/contracts/fixtures/
door2-synthetic-browser.bin` is the frozen example, and the severities, scope
name and attribute keys below are read from it rather than chosen. If this file
and that fixture disagree, the fixture is right.

Chromium runs behind `browser_proxy`, which is the flow's only way onto the
network: every name is resolved and judged there, so a customer's flow cannot
reach an address inside our network however the name it uses resolves. A step
the proxy refused during is a failed step, whatever the page showed.

Playwright is imported inside `perform` rather than at module scope. Only the
browser image installs it, and the envelope in the second half of this file has
to be buildable — and testable — without a 1.5GB browser present.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.logs.v1 import logs_pb2

from nexovue_scheduler.probes.addresses import refusal_reason_for
from nexovue_scheduler.probes.browser_proxy import LISTEN_HOST, PublicAddressProxy
from nexovue_scheduler.probes.urls import reportable_url
from nexovue_scheduler.publish import (
    PROBER_VERSION,
    SCHEMA_URL,
    prober_resource,
    wrap_envelope,
)

CHECK_TYPE_BROWSER: Final = "browser"

# The only engine we drive. WebKit and Firefox would each be a second browser to
# install, a second version to report and a second set of flakes to explain.
BROWSER_NAME: Final = "chromium"

# The only schemes a step may open. The proxy judges where a request goes on
# the network; `file://` and `chrome://` never touch the network, so they are
# refused before the browser is asked. The API refuses them at creation too,
# but this is the door that actually opens, so it decides for itself.
NAVIGABLE_SCHEMES: Final = frozenset({"http", "https"})

# Chromium sends localhost and link-local targets direct, past any proxy, unless
# told not to. This is the telling. Playwright adds it by itself today, but that
# rests on a helper and an environment variable, and the boundary must not.
PROXY_BYPASS_NOTHING: Final = "<-loopback>"

# WebRTC sends UDP straight to an address a page names, which no HTTP proxy can
# carry. Chromium ignores a switch it does not recognise, and the two builds
# Playwright chooses between spell this one differently — so both are passed and
# the boundary does not depend on which binary launched. See ADR 0096.
WEBRTC_PROXIED_ONLY: Final = "disable_non_proxied_udp"

CHROMIUM_EGRESS_ARGUMENTS: Final = (
    # Chromium's own resolver answers "no such name" for everything but the
    # proxy's address. A proxied load never asks it — an HTTP proxy is handed
    # the name, not an address — so this reaches only what resolves locally.
    # Nothing was observed slipping past without it (ADR 0096); it is kept
    # because a resolver that cannot answer cannot leak one.
    f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {LISTEN_HOST}",
    f"--force-webrtc-ip-handling-policy={WEBRTC_PROXIED_ONLY}",
    f"--webrtc-ip-handling-policy={WEBRTC_PROXIED_ONLY}",
)


# The `config` JSONB a customer edits. Steps run in order and stop at the first
# failure, because everything after a failed login is meaningless.
CONFIG_STEPS: Final = "steps"
STEP_ACTION: Final = "action"
STEP_NAME: Final = "name"
STEP_SELECTOR: Final = "selector"
STEP_URL: Final = "url"
STEP_TEXT: Final = "text"

ACTION_GOTO: Final = "goto"
ACTION_CLICK: Final = "click"
ACTION_FILL: Final = "fill"
ACTION_WAIT_FOR: Final = "wait_for"
ACTION_EXPECT_TEXT: Final = "expect_text"

# What each action cannot run without. Stated as data so a malformed step is
# rejected by one rule rather than by five hand-written checks.
REQUIRED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    ACTION_GOTO: (STEP_URL,),
    ACTION_CLICK: (STEP_SELECTOR,),
    ACTION_FILL: (STEP_SELECTOR, STEP_TEXT),
    ACTION_WAIT_FOR: (STEP_SELECTOR,),
    ACTION_EXPECT_TEXT: (STEP_SELECTOR, STEP_TEXT),
}

# A step whose config we cannot run. Reported as the flow's failure rather than
# raised, so the customer sees *why* their check is broken in their own logs
# instead of the check going quietly missing.
ERROR_TYPE_CONFIGURATION: Final = "ConfigurationError"

ATTR_ERROR_TYPE: Final = "error.type"
ATTR_FAILED_STEP: Final = "synthetic.check.failed_step"
ATTR_FAILED_STEP_INDEX: Final = "synthetic.check.failed_step.index"
ATTR_SCREENSHOT_REF: Final = "synthetic.check.screenshot.ref"
ATTR_URL_FULL: Final = "url.full"
ATTR_BROWSER_NAME: Final = "browser.name"
ATTR_BROWSER_VERSION: Final = "browser.version"
ATTR_STEPS_TOTAL: Final = "synthetic.check.steps.total"
ATTR_STEPS_PASSED: Final = "synthetic.check.steps.passed"
ATTR_DURATION: Final = "synthetic.check.duration"

MILLISECONDS_PER_SECOND: Final = 1000


class FlowConfigError(ValueError):
    """A step the customer configured cannot be run as written."""

    def __init__(self, index: int, name: str, message: str) -> None:
        super().__init__(message)
        self.index = index
        self.name = name
        self.message = message


class EgressRefusedError(RuntimeError):
    """The proxy refused an address this step asked for.

    Raised by us rather than Chromium: a refusal over plain HTTP comes back as
    a page, and `goto` would report that it loaded.
    """


class EgressJudge(Protocol):
    """What a step's judgement needs from the proxy: whom it refused meanwhile."""

    def take_refusals(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class BrowserStep:
    """One action in a customer's flow, already validated."""

    name: str
    action: str
    selector: str | None = None
    url: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class StepFailure:
    """Where the flow stopped, and what stopped it."""

    name: str
    index: int
    error_type: str
    message: str
    at_unix_nano: int
    # The page the browser was on when it failed, which is not always the step's
    # own url — a redirect to a login page is exactly the thing worth seeing.
    url: str | None = None
    browser_version: str | None = None
    # Deliberately unset for now; see the module note in the step-4 report.
    screenshot_ref: str | None = None


@dataclass(frozen=True, slots=True)
class FlowResult:
    """One completed browser flow, whether it got to the end or not."""

    check_id: str
    target: str
    steps_total: int
    steps_passed: int
    total_seconds: float
    failure: StepFailure | None = None

    @property
    def up(self) -> bool:
        return self.failure is None


def perform(
    check_id: str,
    target: str,
    timeout_seconds: int,
    config: dict[str, Any] | None = None,
    *,
    private_allowed: bool = False,
) -> FlowResult:
    """Run one browser flow and report how far it got.

    Never raises for a target's behaviour — a page that will not load is a
    result, not an error. It raises when *we* cannot run the check at all:
    Playwright failing to start, or the egress proxy failing to listen. Both are
    news about us rather than about the customer's site, and `http_check` draws
    the same line for the same reason.
    """
    started = time.monotonic()
    try:
        steps = parse_steps(config or {}, target)
    except FlowConfigError as invalid:
        return _config_failure(check_id, target, invalid, config or {}, started)

    return _drive(
        check_id, target, steps, timeout_seconds, started, private_allowed=private_allowed
    )


def parse_steps(config: dict[str, Any], target: str) -> list[BrowserStep]:
    """Read the customer's step list, rejecting anything we cannot run.

    A flow with no steps still means something: open the target and see that it
    loads. That is the check someone gets by filling in only a URL, so it is the
    default rather than an error.
    """
    raw = config.get(CONFIG_STEPS)
    if raw is None:
        url = _navigable_url(0, ACTION_GOTO, target)
        return [BrowserStep(name=ACTION_GOTO, action=ACTION_GOTO, url=url)]
    if not isinstance(raw, list):
        raise FlowConfigError(0, CONFIG_STEPS, f"{CONFIG_STEPS} must be a list of steps")
    return [_parse_step(index, entry) for index, entry in enumerate(raw)]


def _parse_step(index: int, entry: object) -> BrowserStep:
    fallback_name = f"step-{index + 1}"
    if not isinstance(entry, dict):
        raise FlowConfigError(index, fallback_name, "a step must be an object")

    action = entry.get(STEP_ACTION)
    name = str(entry.get(STEP_NAME) or action or fallback_name)
    if not isinstance(action, str) or action not in REQUIRED_FIELDS:
        known = ", ".join(sorted(REQUIRED_FIELDS))
        raise FlowConfigError(index, name, f"unknown action {action!r}; expected one of {known}")

    missing = [field for field in REQUIRED_FIELDS[action] if not entry.get(field)]
    if missing:
        raise FlowConfigError(index, name, f"action {action!r} needs {', '.join(missing)}")

    url = _optional_string(entry.get(STEP_URL))
    return BrowserStep(
        name=name,
        action=action,
        selector=_optional_string(entry.get(STEP_SELECTOR)),
        url=_navigable_url(index, name, url) if url is not None else None,
        text=_optional_string(entry.get(STEP_TEXT)),
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _navigable_url(index: int, name: str, url: str) -> str:
    """A URL the browser may open, or a configuration error naming the step."""
    if urlsplit(url).scheme.lower() not in NAVIGABLE_SCHEMES:
        raise FlowConfigError(index, name, f"a step can only open http or https, not {url!r}")
    return url


def _config_failure(
    check_id: str,
    target: str,
    invalid: FlowConfigError,
    config: dict[str, Any],
    started: float,
) -> FlowResult:
    """A flow that never ran because it could not be read."""
    declared = config.get(CONFIG_STEPS)
    total = len(declared) if isinstance(declared, list) else 1
    return FlowResult(
        check_id=check_id,
        target=target,
        steps_total=total,
        steps_passed=invalid.index,
        total_seconds=time.monotonic() - started,
        failure=StepFailure(
            name=invalid.name,
            index=invalid.index,
            error_type=ERROR_TYPE_CONFIGURATION,
            message=invalid.message,
            at_unix_nano=time.time_ns(),
            url=target,
        ),
    )


def launch_options(proxy_endpoint: str) -> dict[str, Any]:
    """How Chromium is launched: through the proxy, with no other way out.

    Headless is pinned rather than left to Playwright's default because it
    chooses the binary — `chromium-headless-shell` when headless, Chrome for
    Testing when not. They accept different switches, and only one of them is
    what the live suite and the browser image measure.
    """
    return {
        "headless": True,
        "proxy": {"server": proxy_endpoint, "bypass": PROXY_BYPASS_NOTHING},
        "args": list(CHROMIUM_EGRESS_ARGUMENTS),
    }


def _drive(
    check_id: str,
    target: str,
    steps: list[BrowserStep],
    timeout_seconds: int,
    started: float,
    *,
    private_allowed: bool,
) -> FlowResult:
    """Open a browser behind the proxy, run the steps in order, stop at the first failure."""
    # The proxy first, and Playwright only once it is listening. A flow must not
    # start without its boundary, and a proxy that cannot listen raises here
    # rather than after a browser exists that could run without it.
    with PublicAddressProxy(private_allowed=private_allowed) as proxy:
        # Imported here rather than at module scope: only the browser image
        # installs Playwright, and this module's envelope half must import
        # without it.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options(proxy.endpoint))
            page = browser.new_page()
            page.set_default_timeout(timeout_seconds * MILLISECONDS_PER_SECOND)
            try:
                passed, failure = _run_steps(page, steps, target, proxy, browser.version)
            finally:
                browser.close()

    return FlowResult(
        check_id=check_id,
        target=target,
        steps_total=len(steps),
        steps_passed=passed,
        total_seconds=time.monotonic() - started,
        failure=failure,
    )


def _run_steps(
    page: Any, steps: list[BrowserStep], target: str, judge: EgressJudge, browser_version: str
) -> tuple[int, StepFailure | None]:
    """How many steps passed, and what stopped the first one that did not."""
    from playwright.sync_api import Error as PlaywrightError

    for index, step in enumerate(steps):
        try:
            _run_judged_step(page, step, target, judge)
        except (PlaywrightError, AssertionError, EgressRefusedError) as stopped:
            return index, _step_failure(step, index, stopped, page, target, browser_version)
    return _judge_after_the_last_step(page, steps, target, judge, browser_version)


def _judge_after_the_last_step(
    page: Any, steps: list[BrowserStep], target: str, judge: EgressJudge, browser_version: str
) -> tuple[int, StepFailure | None]:
    """A refusal the final step caused has no next step to notice it.

    Chromium fetches subresources asynchronously, so a refusal can land after
    the step that caused it returned. Without this drain a flow whose last step
    was blocked reported `up`.
    """
    if not steps:
        return 0, None
    last = len(steps) - 1
    try:
        _raise_if_refused(judge)
    except EgressRefusedError as refused:
        return last, _step_failure(steps[last], last, refused, page, target, browser_version)
    return len(steps), None


def _step_failure(
    step: BrowserStep,
    index: int,
    stopped: BaseException,
    page: Any,
    target: str,
    browser_version: str,
) -> StepFailure:
    return StepFailure(
        name=step.name,
        index=index,
        error_type=type(stopped).__name__,
        message=str(stopped).strip().splitlines()[0],
        at_unix_nano=time.time_ns(),
        url=page.url or target,
        browser_version=browser_version,
    )


def _run_judged_step(page: Any, step: BrowserStep, target: str, judge: EgressJudge) -> None:
    """One step, judged by the proxy as well as by Chromium.

    A refusal during the step fails it whatever the page showed. When Chromium
    also failed, the refusal is the reason reported: its own message reads as
    the site being down, which is the wrong diagnosis.
    """
    try:
        _run_step(page, step, target)
    except Exception as stopped:
        _raise_if_refused(judge, cause=stopped)
        raise
    _raise_if_refused(judge)


def _raise_if_refused(judge: EgressJudge, cause: BaseException | None = None) -> None:
    reason = refusal_reason_for(judge.take_refusals())
    if reason is not None:
        raise EgressRefusedError(reason) from cause


def _run_step(page: Any, step: BrowserStep, target: str) -> None:
    """Perform one action. Raises Playwright's own error, message and all."""
    if step.action == ACTION_GOTO:
        page.goto(step.url or target)
    elif step.action == ACTION_CLICK:
        page.click(step.selector)
    elif step.action == ACTION_FILL:
        page.fill(step.selector, step.text)
    elif step.action == ACTION_WAIT_FOR:
        page.wait_for_selector(step.selector)
    elif step.action == ACTION_EXPECT_TEXT:
        found = page.text_content(step.selector)
        if step.text not in (found or ""):
            raise AssertionError(f"expected {step.text!r} in {step.selector!r}, found {found!r}")
    else:  # pragma: no cover - parse_steps rejects anything else
        raise FlowConfigError(0, step.name, f"unrunnable action {step.action!r}")


# --------------------------------------------------------------------------
# The envelope. Kept importable without Playwright, so the contract this door
# owes can be asserted offline.
# --------------------------------------------------------------------------


def build_envelope(
    result: FlowResult,
    *,
    org_id: str,
    project_id: str,
    observed_at_unix_nano: int,
    environment: str,
    region: str,
) -> bytes:
    """Encode one browser flow as a serialized `nexovue.v1.Envelope` of logs."""
    request = logs_service_pb2.ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_logs.schema_url = SCHEMA_URL
    resource_logs.resource.CopyFrom(
        prober_resource(
            check_id=result.check_id,
            check_type=CHECK_TYPE_BROWSER,
            environment=environment,
            region=region,
        )
    )

    scope_logs = resource_logs.scope_logs.add()
    scope_logs.schema_url = SCHEMA_URL
    scope_logs.scope.name = f"nexovue.scheduler.checks.{CHECK_TYPE_BROWSER}"
    scope_logs.scope.version = PROBER_VERSION
    _add_records(scope_logs, result, observed_at_unix_nano)

    return wrap_envelope(
        request.SerializeToString(),
        door=envelope_pb2.DOOR_SYNTHETIC,
        # Logs, not metrics: a flow's result is which step broke and what it
        # said, and a number cannot carry that.
        signal=envelope_pb2.SIGNAL_LOGS,
        org_id=org_id,
        project_id=project_id,
        observed_at_unix_nano=observed_at_unix_nano,
    )


def _add_records(scope_logs: logs_pb2.ScopeLogs, result: FlowResult, at_unix_nano: int) -> None:
    """The failure, if there was one, and always the summary.

    The summary is emitted even for a healthy flow: a run that says "4 of 4
    passed" is what makes a *missing* run visible, and silence on success would
    make "nobody looked" indistinguishable from "nothing was wrong".
    """
    if result.failure is not None:
        _add_failure_record(scope_logs, result.failure)
    _add_summary_record(scope_logs, result, at_unix_nano)


def _add_failure_record(scope_logs: logs_pb2.ScopeLogs, failure: StepFailure) -> None:
    record = scope_logs.log_records.add(
        time_unix_nano=failure.at_unix_nano,
        observed_time_unix_nano=failure.at_unix_nano,
        severity_number=logs_pb2.SEVERITY_NUMBER_ERROR,
        severity_text="ERROR",
    )
    record.body.string_value = f"step {failure.name!r} failed: {failure.message}"
    record.attributes.extend(
        _attributes(
            {
                ATTR_ERROR_TYPE: failure.error_type,
                ATTR_FAILED_STEP: failure.name,
                ATTR_FAILED_STEP_INDEX: failure.index,
                # Absent rather than empty when the browser never started or the
                # screenshot was not kept: an empty ref reads as a broken link.
                ATTR_SCREENSHOT_REF: failure.screenshot_ref,
                # Credentials removed and signatures redacted at the one point the
                # URL leaves us, so both StepFailure sites are covered. ADR 0103.
                ATTR_URL_FULL: reportable_url(failure.url),
                ATTR_BROWSER_NAME: BROWSER_NAME,
                ATTR_BROWSER_VERSION: failure.browser_version,
            }
        )
    )


def _add_summary_record(
    scope_logs: logs_pb2.ScopeLogs, result: FlowResult, at_unix_nano: int
) -> None:
    # INFO even when steps failed, which the fixture is explicit about: this
    # record says the run completed and how far it got. The failure record above
    # is what carries the alarm, and raising this one too would double it.
    record = scope_logs.log_records.add(
        time_unix_nano=at_unix_nano,
        observed_time_unix_nano=at_unix_nano,
        severity_number=logs_pb2.SEVERITY_NUMBER_INFO,
        severity_text="INFO",
    )
    record.body.string_value = (
        f"browser flow finished: {result.steps_passed} of {result.steps_total} steps passed"
    )
    record.attributes.extend(
        _attributes(
            {
                ATTR_STEPS_TOTAL: result.steps_total,
                ATTR_STEPS_PASSED: result.steps_passed,
                ATTR_DURATION: result.total_seconds,
            }
        )
    )


def _attributes(values: dict[str, object]) -> list[common_pb2.KeyValue]:
    """Typed OTLP attributes, skipping anything not measured.

    A key we have no value for is omitted rather than sent empty: a zero or an
    empty string reads as a reading that was taken, which is a different claim.
    """
    attributes = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):  # bool is an int; it would encode as 0/1
            any_value = common_pb2.AnyValue(bool_value=value)
        elif isinstance(value, int):
            any_value = common_pb2.AnyValue(int_value=value)
        elif isinstance(value, float):
            any_value = common_pb2.AnyValue(double_value=value)
        else:
            any_value = common_pb2.AnyValue(string_value=str(value))
        attributes.append(common_pb2.KeyValue(key=key, value=any_value))
    return attributes
