"""Why a check failed, in words an alert may carry (BR9, FR18).

A run's own error is written for the person reading its evidence page: *expected
body contains "ok", got {"token": …}* quotes what the response said, which is
exactly what an alert must never carry into Slack or an inbox. This builds the
sentence again from the run's structured evidence and keeps only what describes
the service rather than its content — a status code, a time, a certificate's
days left. The content stays behind the link (FR16).
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.checks.evidence import AssertionResult, RunEvidence, StepEvidence

# What an assertion may report the actual value of: facts about the service, not
# what it returned. A header, a body or a JSON value is the customer's data.
_REPORTABLE_SOURCES: Final = frozenset({"status_code", "response_time", "tls_days_remaining"})

_SOURCE_WORDS: Final = {
    "status_code": "status code",
    "response_time": "response time",
    "tls_days_remaining": "TLS certificate days remaining",
    "header": "header",
    "body": "body",
    "json_path": "JSON value at",
}


def alert_reason(evidence: RunEvidence) -> str | None:
    """The run's failure, safe to send. None when the run did not fail."""
    failing = next((step for step in evidence.steps if not step.passed), None)
    if failing is None:
        # No step failed: degraded by time, misconfigured before sending, or a
        # heartbeat. Each of those errors is written by us from numbers and
        # configuration, never from a response.
        return evidence.error
    described = _describe_step(failing)
    if evidence.check_type == "multistep" and described:
        return f"request {failing.position + 1}: {described}"
    return described


def _describe_step(step: StepEvidence) -> str | None:
    failed = next((result for result in step.assertions if not result.passed), None)
    if failed is None:
        # The request itself failed — refused, timed out, not resolvable — and the
        # message is the transport's, which names no response content.
        return step.error
    return _describe_assertion(failed)


def _describe_assertion(result: AssertionResult) -> str:
    subject = " ".join(
        part for part in (_SOURCE_WORDS.get(result.source, result.source), result.property) if part
    )
    comparison = result.comparison.replace("_", " ")
    if result.source in _REPORTABLE_SOURCES:
        actual = "nothing" if result.actual is None else result.actual
        return f"expected {subject} {comparison} {result.target}, got {actual}".replace("  ", " ")
    # Neither what was expected nor what arrived: a target can quote the content
    # it looks for, and the actual value is the response's own.
    return f"the {subject} assertion ({comparison}) failed"
