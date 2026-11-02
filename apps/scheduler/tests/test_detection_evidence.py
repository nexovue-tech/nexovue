"""FR3's evidence endpoints, offline: which endpoint a finding names against the same
window last week. The store's read is proved in `test_detection_project_job_live.py`."""

from __future__ import annotations

from nexovue_scheduler.detection.project_job import failing_evidence, slower_evidence
from nexovue_scheduler.detection.store import EndpointWeeks


def _weeks(
    requests: int,
    failures: int,
    week_requests: int,
    week_failures: int,
    *,
    p95: float | None = None,
    week_p95: float | None = None,
) -> EndpointWeeks:
    return EndpointWeeks(
        requests=requests,
        failures=failures,
        p95_ms=p95,
        week_requests=week_requests,
        week_failures=week_failures,
        week_p95_ms=week_p95,
    )


def test_failing_names_the_endpoint_with_most_failures_beyond_last_week_s_share() -> None:
    found = failing_evidence(
        {
            # Twice the traffic at last week's 10%: more failures, none of them extra.
            "GET /cart": _weeks(400, 40, 200, 20),
            "POST /pay": _weeks(100, 30, 100, 0),
        }
    )

    assert found == ("POST /pay", 30.0, 0.0)


def test_failing_needs_the_request_floor_in_both_windows() -> None:
    assert failing_evidence({"POST /pay": _weeks(100, 90, 19, 0)}) is None
    assert failing_evidence({"POST /pay": _weeks(19, 19, 100, 0)}) is None


def test_failing_names_nothing_when_no_endpoint_fails_more_than_last_week() -> None:
    assert failing_evidence({"GET /": _weeks(100, 5, 100, 10)}) is None


def test_failing_breaks_a_tie_by_name() -> None:
    found = failing_evidence({"b": _weeks(50, 10, 50, 0), "a": _weeks(50, 10, 50, 0)})

    assert found is not None and found[0] == "a"


def test_slower_names_the_largest_rise_in_milliseconds_not_the_largest_ratio() -> None:
    found = slower_evidence(
        {
            "GET /fast": _weeks(40, 0, 40, 0, p95=5.0, week_p95=1.0),
            "POST /pay": _weeks(30, 0, 30, 0, p95=400.0, week_p95=100.0),
        }
    )

    assert found == ("POST /pay", 400.0, 100.0)
