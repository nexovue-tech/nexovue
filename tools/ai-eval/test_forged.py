"""`--run forged`: the forged exports, the two arms and the counts (F3 plan T2.16; AC76). The intake
is a fake that records what it was sent; the live run waits for the dev stack (status.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forged import (
    AIMED_AT,
    FORGED_CALLEE,
    FORGED_EXCEPTION,
    FORGED_VERSION,
    FORGERIES,
    SPANS_PER_EXPORT,
    Forgery,
    Observed,
    Spec,
    Trial,
    export_request,
    poster,
    render,
    require_local,
    run_trials,
    specs_for,
    tally,
)
from nexovue_platform.evidence import RULE_NEW_ERROR, RULE_TRAFFIC_ROSE
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from nexovue_scheduler.evidence.rules import Cause

AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def parsed(spec: Spec) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    request.ParseFromString(export_request(spec))
    return request


def attributes(items: object) -> dict[str, str]:
    return {each.key: each.value.string_value for each in items}  # type: ignore[attr-defined]


def test_a_forged_edge_is_a_burst_of_failed_client_calls_to_a_callee_that_does_not_exist() -> None:
    request = parsed(Spec("edge_failures", "checkout", AT, outage=True))

    [resource] = request.resource_spans
    spans = resource.scope_spans[0].spans
    assert attributes(resource.resource.attributes)["service.name"] == "checkout"
    assert len(spans) == SPANS_PER_EXPORT
    assert all(s.kind == Span.SPAN_KIND_CLIENT and s.status.code == 2 for s in spans)
    assert all(attributes(s.attributes)["peer.service"] == FORGED_CALLEE for s in spans)


def test_a_forged_version_rides_on_the_resource_and_a_forged_error_on_an_exception_event() -> None:
    version = parsed(Spec("new_version", "ad", AT, outage=False))
    error = parsed(Spec("new_error", "ad", AT, outage=False))

    assert attributes(version.resource_spans[0].resource.attributes)["service.version"] == (
        FORGED_VERSION
    )
    [event] = error.resource_spans[0].scope_spans[0].spans[0].events
    assert event.name == "exception"
    assert attributes(event.attributes)["exception.type"] == FORGED_EXCEPTION


def test_a_traffic_burst_is_plain_server_spans_with_no_failure() -> None:
    spans = (
        parsed(Spec("traffic_burst", "ad", AT, outage=False)).resource_spans[0].scope_spans[0].spans
    )

    assert all(s.kind == Span.SPAN_KIND_SERVER and s.status.code == 0 for s in spans)


def test_every_forgery_names_the_rule_it_is_aimed_at() -> None:
    assert set(AIMED_AT) == set(FORGERIES)
    assert (
        AIMED_AT["traffic_burst"] == RULE_TRAFFIC_ROSE and AIMED_AT["new_error"] == RULE_NEW_ERROR
    )


def test_the_specs_cover_every_forgery_in_both_arms_for_every_service_and_repeat() -> None:
    specs = specs_for(["ad", "cart"], start=AT, repeats=2, spacing=timedelta(minutes=30))

    assert len(specs) == 2 * 2 * len(FORGERIES) * 2
    assert {(s.forgery, s.outage) for s in specs} == {
        (f, o) for f in FORGERIES for o in (True, False)
    }
    assert len({s.at for s in specs}) == len(specs)


def cause(rule: str) -> Cause:
    return Cause("e", rule, "possible", "telemetry")


async def test_the_report_counts_findings_opened_and_false_causes_by_rule_in_each_arm() -> None:
    answers: dict[tuple[Forgery, bool], Observed] = {
        ("edge_failures", True): Observed(True, cause("dependency_edge")),
        ("edge_failures", False): Observed(True, None),
        ("new_version", True): Observed(True, None),
        ("new_version", False): Observed(False, None),
        ("new_error", False): Observed(True, cause("new_error")),
    }
    sent: list[bytes] = []

    async def observe(spec: Spec) -> Observed:
        return answers[(spec.forgery, spec.outage)]

    def post(body: bytes) -> int:
        sent.append(body)
        return 200

    specs = [
        Spec(forgery, "checkout", AT + timedelta(minutes=number), outage)
        for number, (forgery, outage) in enumerate(answers)
    ]

    trials = await run_trials(specs, post=post, observe=observe)
    report = tally(trials)

    assert len(sent) == 5
    # A cause with no outage is not a false cause: there is no outage to blame it on.
    assert report.arms[("new_error", False)].false_causes == 0
    assert report.arms[("edge_failures", True)].false_causes == 1
    assert report.arms[("edge_failures", False)].findings_opened == 1
    assert (
        report.arms[("edge_failures", False)].false_causes == 0
    )  # no outage: a cause is not counted
    assert report.arms[("new_version", False)].findings_opened == 0
    assert report.false_by_rule == {"dependency_edge": 1}
    assert "| edge_failures | dependency_edge | yes | 1 | 0 | 1 | 1 |" in render(report)


async def test_an_export_the_intake_refuses_is_a_trial_of_its_own_and_is_never_observed() -> None:
    observed: list[Spec] = []

    async def observe(spec: Spec) -> Observed:
        observed.append(spec)
        return Observed(True, None)

    [trial] = await run_trials(
        [Spec("new_error", "ad", AT, outage=True)], post=lambda _body: 401, observe=observe
    )

    assert not trial.accepted and trial.observed is None and observed == []
    assert tally([trial]).arms[("new_error", True)].refused == 1


def test_a_trial_without_an_observation_never_counts_as_a_finding() -> None:
    trial = Trial(Spec("new_error", "ad", AT, outage=True), accepted=True, observed=None)

    assert tally([trial]).arms[("new_error", True)].refused == 1


def test_forged_spans_are_only_ever_sent_to_a_receiver_on_this_machine() -> None:
    require_local("http://localhost:14318")
    with pytest.raises(SystemExit, match="refusing receiver host"):
        require_local("https://ingest.example.com")
    with pytest.raises(SystemExit, match="refusing receiver host"):
        poster("https://ingest.example.com", "key")
