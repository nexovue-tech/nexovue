"""One run of any kind of check: perform it, judge it, and describe what happened.

The outcome is decided here, in one order, for every kind:

1. The check could not run as written — **misconfigured**, the author's to fix.
2. We could not perform it — **prober_error**, ours.
3. No response, a failed assertion, or slower than the failing threshold — **down**.
4. Everything passed but slower than the degraded threshold — **degraded**.
5. Otherwise **up**.

Nothing here publishes, retries or writes state; `tasks.py` decides those from
what this returns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nexovue_platform.checks.config import (
    ApiCheckConfig,
    Assertion,
    CheckConfig,
    DnsCheckConfig,
    MultistepCheckConfig,
    TcpCheckConfig,
    parse_config,
    requests_of,
)
from nexovue_platform.checks.evidence import (
    AssertionResult,
    Outcome,
    ReceivedResponse,
    RunEvidence,
    StepEvidence,
)
from nexovue_platform.checks.task import CheckDefinition
from nexovue_platform.checks.templating import UnresolvedReferenceError, substitute
from nexovue_platform.telemetry.logging import get_logger
from pydantic import ValidationError

from nexovue_scheduler.probes import assertions, dns_check, http_check, request_builder, tcp_check
from nexovue_scheduler.probes.failures import MisconfiguredError, ProberError
from nexovue_scheduler.probes.redaction import Redactor
from nexovue_scheduler.probes.result import Certificate, HttpExchange, RunMeasurement
from nexovue_scheduler.probes.secrets import open_secrets
from nexovue_scheduler.probes.urls import reportable_url

MILLISECONDS_PER_SECOND: Final = 1000.0

# What the customer reads when the fault is ours and unforeseen. The detail goes to
# our log with the check id; an exception's text could carry anything.
UNEXPECTED_FAILURE: Final = "the prober hit an unexpected error and could not complete this run"

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunContext:
    location: str
    attempt: int
    run_id: str
    started_at: datetime
    maintenance: bool
    private_allowed: bool
    secret_key: str
    files: request_builder.FileSource
    dns_resolvers: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")


@dataclass(slots=True)
class _Progress:
    """What a run has found so far, as its steps accumulate."""

    steps: list[StepEvidence] = field(default_factory=list)
    outcome: Outcome = "up"
    error: str | None = None
    # None until something was actually timed, so a run refused before its first
    # request reports no duration rather than an instant one.
    total_seconds: float | None = None
    phases: dict[str, float] = field(default_factory=dict)
    status_code: int | None = None
    certificate: Certificate | None = None

    def record_seconds(self, seconds: float) -> None:
        self.total_seconds = (self.total_seconds or 0.0) + seconds

    def add_phases(self, phases: dict[str, float]) -> None:
        for phase, seconds in phases.items():
            self.phases[phase] = self.phases.get(phase, 0.0) + seconds

    def fail(self, outcome: Outcome, reason: str) -> None:
        if self.outcome == "up":
            self.outcome, self.error = outcome, reason


def run(definition: CheckDefinition, context: RunContext) -> tuple[RunEvidence, RunMeasurement]:
    """Perform the run. Secrets opened here are redacted from what is returned."""
    progress = _Progress()
    redactor = Redactor(())
    try:
        config = _config_of(definition)
        secrets = open_secrets(
            definition.sealed_secrets,
            project_id=definition.project_id,
            configured_key=context.secret_key,
        )
        redactor = Redactor(secrets.values())
        _perform(config, definition, context, secrets, redactor, progress)
        if progress.outcome == "up":
            _judge_speed(definition, progress)
    except MisconfiguredError as wrong:
        progress.outcome, progress.error = "misconfigured", str(wrong)
    except ProberError as ours:
        progress.outcome, progress.error = "prober_error", str(ours)
    except Exception:
        # A run always ends in an outcome. One that escaped would publish nothing and
        # record nothing, and Run now would wait for a result that never comes.
        logger.exception("a check run failed unexpectedly", check_id=definition.check_id)
        progress.outcome, progress.error = "prober_error", UNEXPECTED_FAILURE

    evidence = RunEvidence(
        run_id=context.run_id,
        check_id=definition.check_id,
        check_type=definition.type,
        location=context.location,
        attempt=context.attempt,
        final=True,
        maintenance=context.maintenance,
        outcome=progress.outcome,
        started_at=context.started_at,
        duration_ms=(
            None
            if progress.total_seconds is None
            else progress.total_seconds * MILLISECONDS_PER_SECOND
        ),
        status_code=progress.status_code,
        tls_days_remaining=(
            progress.certificate.days_remaining if progress.certificate is not None else None
        ),
        steps=progress.steps,
        error=progress.error,
    )
    measurement = RunMeasurement(
        check_id=definition.check_id or "",
        check_type=definition.type,
        run_id=context.run_id,
        outcome=progress.outcome,
        attempt=context.attempt,
        final=True,
        maintenance=context.maintenance,
        location=context.location,
        total_seconds=progress.total_seconds,
        status_code=progress.status_code,
        phases=progress.phases,
        certificate=progress.certificate,
    )
    return redactor.model(evidence), measurement


def _config_of(definition: CheckDefinition) -> CheckConfig:
    try:
        return parse_config(definition.config)
    except ValidationError as unreadable:
        raise MisconfiguredError(
            "the stored configuration is not one this prober can read"
        ) from unreadable


def _perform(
    config: CheckConfig,
    definition: CheckDefinition,
    context: RunContext,
    secrets: dict[str, str],
    redactor: Redactor,
    progress: _Progress,
) -> None:
    if isinstance(config, ApiCheckConfig | MultistepCheckConfig):
        _perform_requests(config, definition, context, secrets, redactor, progress)
    elif isinstance(config, TcpCheckConfig):
        _perform_tcp(config, context, progress)
    elif isinstance(config, DnsCheckConfig):
        _perform_dns(config, context, progress)
    else:
        raise MisconfiguredError(f"a {config.type} check is not probed")


def _perform_requests(
    config: ApiCheckConfig | MultistepCheckConfig,
    definition: CheckDefinition,
    context: RunContext,
    secrets: dict[str, str],
    redactor: Redactor,
    progress: _Progress,
) -> None:
    variables = dict(definition.variables)

    for position, request in enumerate(requests_of(config)):
        try:
            built = request_builder.build(
                request,
                org_id=definition.org_id,
                variables=variables,
                secrets=secrets,
                files=context.files,
                file_references=definition.files,
                redact=redactor.text,
            )
        except MisconfiguredError as wrong:
            progress.steps.append(
                StepEvidence(
                    position=position,
                    name=request.name,
                    target=reportable_url(request.url) or request.url,
                    passed=False,
                    error=str(wrong),
                )
            )
            raise

        exchange = http_check.perform(built.prepared, private_allowed=context.private_allowed)
        progress.record_seconds(exchange.total_seconds)
        progress.add_phases(exchange.phases)
        progress.status_code = exchange.status_code
        progress.certificate = exchange.certificate or progress.certificate

        view = assertions.ResponseView(exchange)
        results: list[AssertionResult] = []
        extracted: list[str] = []
        error = exchange.error
        if error is None:
            results = [
                assertions.evaluate(_filled(assertion, variables, secrets), view)
                for assertion in request.assertions
            ]
            failed = next((result for result in results if not result.passed), None)
            if failed is not None:
                error = describe_failure(failed, redactor.text)
            else:
                for extraction in request.extractions:
                    try:
                        variables[extraction.name] = assertions.extract(extraction, view)
                    except LookupError as missing:
                        error = str(missing)
                        break
                    extracted.append(extraction.name)

        progress.steps.append(
            StepEvidence(
                position=position,
                name=request.name,
                target=reportable_url(exchange.effective_url) or built.sent.url,
                duration_ms=exchange.total_seconds * MILLISECONDS_PER_SECOND,
                phases_ms={
                    phase: seconds * MILLISECONDS_PER_SECOND
                    for phase, seconds in exchange.phases.items()
                },
                request=built.sent,
                response=_received(exchange, redactor),
                assertions=results,
                extracted=extracted,
                passed=error is None,
                error=error,
            )
        )
        if error is not None:
            step = f"request {position + 1}" if isinstance(config, MultistepCheckConfig) else ""
            progress.fail("down", f"{step}: {error}" if step else error)
            return


def _perform_tcp(config: TcpCheckConfig, context: RunContext, progress: _Progress) -> None:
    outcome = tcp_check.perform(
        config.host, config.port, config.timeout_seconds, private_allowed=context.private_allowed
    )
    progress.record_seconds(outcome.total_seconds)
    progress.add_phases(outcome.phases)
    progress.steps.append(
        StepEvidence(
            position=0,
            target=f"{config.host}:{config.port}",
            duration_ms=outcome.total_seconds * MILLISECONDS_PER_SECOND,
            phases_ms={
                phase: seconds * MILLISECONDS_PER_SECOND
                for phase, seconds in outcome.phases.items()
            },
            answers=[outcome.address] if outcome.address else [],
            passed=outcome.connected,
            error=outcome.error,
        )
    )
    if not outcome.connected:
        progress.fail("down", outcome.error or "the connection was not accepted")


def _perform_dns(config: DnsCheckConfig, context: RunContext, progress: _Progress) -> None:
    outcome = dns_check.perform(
        config.name,
        config.record_type,
        config.resolver,
        config.timeout_seconds,
        default_resolvers=list(context.dns_resolvers),
        private_allowed=context.private_allowed,
    )
    progress.record_seconds(outcome.total_seconds)
    progress.add_phases(outcome.phases)
    results = [
        _dns_assertion(assertion.comparison, assertion.target, outcome.answers)
        for assertion in config.assertions
    ]
    error = outcome.error
    if error is None:
        failed = next((result for result in results if not result.passed), None)
        if failed is not None:
            error = describe_failure(failed, _unchanged)
    progress.steps.append(
        StepEvidence(
            position=0,
            target=f"{config.name} {config.record_type}",
            duration_ms=outcome.total_seconds * MILLISECONDS_PER_SECOND,
            answers=outcome.answers,
            assertions=results,
            passed=error is None,
            error=error,
        )
    )
    if error is not None:
        progress.fail("down", error)


def _dns_assertion(comparison: str, target: str, answers: list[str]) -> AssertionResult:
    matchers: dict[str, Callable[[str], bool]] = {
        "contains": lambda answer: answer == target,
        "not_contains": lambda answer: answer == target,
        "matches": lambda answer: assertions.compare("matches", answer, target),
    }
    found = any(matchers[comparison](answer) for answer in answers)
    return AssertionResult(
        source="dns_answer",
        comparison=comparison,
        target=target,
        actual=", ".join(answers) if answers else None,
        passed=not found if comparison == "not_contains" else found,
    )


def _judge_speed(definition: CheckDefinition, progress: _Progress) -> None:
    if progress.total_seconds is None:
        return  # nothing was timed, so there is no speed to judge
    elapsed_ms = progress.total_seconds * MILLISECONDS_PER_SECOND
    if definition.failing_ms is not None and elapsed_ms > definition.failing_ms:
        limit = definition.failing_ms
        progress.fail(
            "down", f"took {round(elapsed_ms)} ms, over the failing threshold of {limit} ms"
        )
    elif definition.degraded_ms is not None and elapsed_ms > definition.degraded_ms:
        progress.outcome = "degraded"
        limit = definition.degraded_ms
        progress.error = f"took {round(elapsed_ms)} ms, over the degraded threshold of {limit} ms"


def _filled(assertion: Assertion, variables: dict[str, str], secrets: dict[str, str]) -> Assertion:
    """An assertion's expected value with its `{{name}}`s filled in, as the request's were."""
    try:
        target = substitute(assertion.target, variables=variables, secrets=secrets)
    except UnresolvedReferenceError as undefined:
        raise MisconfiguredError(
            f"{{{{{undefined.reference}}}}} in an assertion is not defined"
        ) from undefined
    return (
        assertion if target == assertion.target else assertion.model_copy(update={"target": target})
    )


def _unchanged(text: str) -> str:
    return text


def _received(exchange: HttpExchange, redactor: Redactor) -> ReceivedResponse | None:
    if exchange.status_code is None:
        return None
    body = request_builder.capture_body(exchange.body, redactor.text)
    if body is not None and exchange.body_truncated:
        body = body.model_copy(update={"truncated": True})
    return ReceivedResponse(
        status_code=exchange.status_code,
        headers=[
            header.model_copy(update={"value": redactor.text(header.value)})
            for header in request_builder.captured_headers(exchange.headers)
        ],
        body=body,
    )


def describe_failure(result: AssertionResult, redact: Callable[[str], str]) -> str:
    """*expected status code less than 400, got 503* — the sentence a failure is read as.

    Redacted before it is shortened, so a secret the response echoed cannot survive
    in half."""
    subject = f"{result.source.replace('_', ' ')} {result.property}".strip()
    expected = f"{result.comparison.replace('_', ' ')} {result.target}".strip()
    actual = "nothing" if result.actual is None else _short(redact(result.actual))
    return redact(f"expected {subject} {expected}, got {actual}")


def _short(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"
