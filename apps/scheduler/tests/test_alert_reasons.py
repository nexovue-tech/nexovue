"""An alert's reason carries facts about the service, never its content (BR9, AC24)."""

from __future__ import annotations

from datetime import UTC, datetime

from nexovue_platform.checks.evidence import AssertionResult, RunEvidence, StepEvidence

from nexovue_scheduler.alerts.reasons import alert_reason

SECRET_BODY = '{"token": "sk_live_do_not_leak", "status": "degraded"}'


def _run(*steps: StepEvidence, check_type: str = "api", error: str | None = None) -> RunEvidence:
    return RunEvidence(
        run_id="run-1",
        check_id="check-1",
        check_type=check_type,
        location="local",
        attempt=1,
        final=True,
        outcome="down",
        started_at=datetime.now(UTC),
        steps=list(steps),
        error=error,
    )


def _step(
    *assertions: AssertionResult, error: str | None = None, position: int = 0
) -> StepEvidence:
    return StepEvidence(
        position=position,
        target="https://api.example.test/health",
        assertions=list(assertions),
        passed=False,
        error=error,
    )


def test_a_status_code_failure_reads_as_the_status() -> None:
    failed = AssertionResult(
        source="status_code", comparison="less_than", target="400", actual="503", passed=False
    )
    assert alert_reason(_run(_step(failed))) == "expected status code less than 400, got 503"


def test_a_body_failure_names_the_assertion_and_nothing_the_body_said() -> None:
    failed = AssertionResult(
        source="body",
        comparison="contains",
        target='"status": "ok"',
        actual=SECRET_BODY,
        passed=False,
    )
    reason = alert_reason(
        _run(_step(failed, error=f"expected body contains ok, got {SECRET_BODY}"))
    )
    assert reason == "the body assertion (contains) failed"
    assert "sk_live" not in (reason or "")
    assert "status" not in (reason or "")


def test_a_header_or_json_failure_keeps_the_values_out_too() -> None:
    header = AssertionResult(
        source="header",
        property="X-Api-Key",
        comparison="equals",
        target="expected-key",
        actual="actual-secret-key",
        passed=False,
    )
    json_value = AssertionResult(
        source="json_path",
        property="$.account.email",
        comparison="equals",
        target="a@b.c",
        actual="customer@example.test",
        passed=False,
    )
    for failed in (header, json_value):
        reason = alert_reason(_run(_step(failed))) or ""
        assert failed.actual is not None and failed.actual not in reason
        assert failed.target not in reason


def test_a_transport_failure_is_the_transports_own_words() -> None:
    step = _step(error="Failed to connect to api.example.test port 443: Connection refused")
    assert alert_reason(_run(step)) == step.error


def test_a_run_with_no_failed_step_carries_the_runs_own_reason() -> None:
    reason = "took 2300 ms, over the degraded threshold of 2000 ms"
    assert alert_reason(_run(error=reason)) == reason


def test_a_multistep_failure_says_which_request() -> None:
    failed = AssertionResult(
        source="status_code", comparison="equals", target="200", actual="401", passed=False
    )
    reason = alert_reason(_run(_step(failed, position=2), check_type="multistep"))
    assert reason == "request 3: expected status code equals 200, got 401"
