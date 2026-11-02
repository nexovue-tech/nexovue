"""Which services a project judges when it has more than the cap (design, *Security,
tenancy, and privacy*: "a project with more than 500 judged services judges the 500
busiest, and shows *too many services to judge all* on the rest").

The choice is deterministic: the same loads give the same services every run. That a
run marks the rest, and a service back under the cap is judged again, is proved
against the stores in `test_detection_project_job_live.py`.
"""

from __future__ import annotations

from nexovue_platform.detection import MAX_JUDGED_SERVICES

from nexovue_scheduler.detection.project_job import ServiceLoad, busiest


def _load(
    name: str, *, requests: int = 0, expected: float = 0.0, holds_a_finding: bool = False
) -> ServiceLoad:
    return ServiceLoad(
        name=name, requests=requests, expected=expected, holds_a_finding=holds_a_finding
    )


def test_the_cap_is_the_design_s_500() -> None:
    assert MAX_JUDGED_SERVICES == 500


def test_a_project_under_the_cap_judges_every_service() -> None:
    judged, over = busiest([_load("a"), _load("b", requests=5)], cap=2)

    assert sorted(judged) == ["a", "b"]
    assert over == []


def test_past_the_cap_the_busiest_are_judged_and_the_rest_are_not() -> None:
    loads = [_load("quiet", requests=1), _load("busy", requests=900), _load("mid", requests=40)]

    judged, over = busiest(loads, cap=2)

    assert judged == ["busy", "mid"]
    assert over == ["quiet"]


def test_a_service_that_stopped_keeps_its_place_by_what_its_normal_expects() -> None:
    """Ranked on this window alone, a stop would fall out of judging at the moment it
    should open a finding (D30). The normal's traffic holds its place."""
    loads = [_load("stopped", requests=0, expected=600.0), _load("steady", requests=50)]

    judged, over = busiest(loads, cap=1)

    assert judged == ["stopped"]
    assert over == ["steady"]


def test_a_service_with_an_open_finding_is_judged_first_so_its_finding_can_close() -> None:
    loads = [_load("busy", requests=900), _load("troubled", requests=2, holds_a_finding=True)]

    judged, over = busiest(loads, cap=1)

    assert judged == ["troubled"]
    assert over == ["busy"]


def test_equal_loads_are_decided_by_name_so_every_run_chooses_the_same() -> None:
    loads = [_load(name, requests=10) for name in ("delta", "alpha", "charlie", "bravo")]

    first = busiest(loads, cap=2)
    again = busiest(list(reversed(loads)), cap=2)

    assert first == (["alpha", "bravo"], ["charlie", "delta"])
    assert again == first
