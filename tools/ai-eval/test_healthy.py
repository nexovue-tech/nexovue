"""The healthy run's sampled windows (F3 plan T2.16; AC16(b)). The reader is a fake over `Facts`;
`apps/scheduler/tests/test_bed_live.py` reads real stores through the evidence job's own read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bed_builders import ZONE, facts
from healthy import (
    KEEP_CLEAR,
    RealFinding,
    SampledWindow,
    finding_for,
    render,
    run_healthy,
    sample_windows,
)
from nexovue_platform.evidence import RULE_SIGNED_DEPLOY, RULE_SIGNED_DEPLOY_WEAK

from nexovue_scheduler.evidence.rules import DeployFact, DeployHistory, Facts, FindingInput

START = datetime(2026, 11, 13, tzinfo=UTC)
END = START + timedelta(days=7)
SERVICES = ["ad", "cart", "checkout"]


def windows(per_service: int = 20, real: tuple[RealFinding, ...] = ()) -> list[SampledWindow]:
    return sample_windows(SERVICES, start=START, end=END, per_service=per_service, real=real)


def test_each_service_gets_the_stated_number_of_distinct_whole_minute_windows() -> None:
    sampled = windows(20)

    for service in SERVICES:
        times = [w.at for w in sampled if w.service == service]
        assert len(times) == 20 == len(set(times))
        assert all(START <= at < END and at.second == 0 for at in times)


def test_asking_for_every_minute_of_a_short_span_gives_each_exactly_once() -> None:
    sampled = sample_windows(["ad"], start=START, end=START + timedelta(minutes=30), per_service=30)

    assert [w.at for w in sampled] == [START + timedelta(minutes=n) for n in range(30)]


def test_the_same_seed_samples_the_same_windows() -> None:
    assert windows() == windows()


def test_windows_keep_clear_of_a_real_finding_on_the_same_service_only() -> None:
    real = RealFinding(
        "ad", START + timedelta(days=2), START + timedelta(days=2, hours=1), cause=None
    )
    crowded = sample_windows(
        ["ad", "cart"],
        start=START + timedelta(days=2),
        end=START + timedelta(days=2, hours=8),
        per_service=30,
        real=(real,),
    )

    ad = [w.at for w in crowded if w.service == "ad"]
    assert all(
        not (real.started_at - KEEP_CLEAR <= at < (real.closed_at or END) + KEEP_CLEAR) for at in ad
    )
    assert any(w.service == "cart" and real.started_at <= w.at < real.closed_at for w in crowded)  # type: ignore[operator]


def test_a_window_is_read_as_a_finding_that_opened_there() -> None:
    window = SampledWindow("ad", START)

    finding = finding_for(window)

    assert finding.service == "ad" and finding.started_at == START and finding.scope == "service"
    assert finding.id.startswith("healthy-ad-")


async def test_no_cause_on_any_window_is_a_clean_result_with_the_windows_counted() -> None:
    async def reader(finding: FindingInput) -> Facts:
        return facts(finding.service or "ad")

    result = await run_healthy(reader, windows(10))

    assert result.windows == 30 and result.clean and not result.false_causes
    assert "**30**" in render(result)


async def test_every_cause_stated_on_a_healthy_window_is_a_false_cause_named_by_rule() -> None:
    async def reader(finding: FindingInput) -> Facts:
        # A deploy of the service four minutes before: rule 1 names it, on a healthy window.
        service = finding.service or "ad"
        at = finding.started_at - timedelta(minutes=4)
        return Facts(
            finding=finding,
            now=finding.started_at + timedelta(minutes=5),
            zone=ZONE,
            deploys=(DeployFact("d", service, "v1", at, True),),
            histories={service: DeployHistory(service, (), finding.started_at - timedelta(days=8))},
        )

    result = await run_healthy(reader, windows(3))

    assert not result.clean
    assert {each.rule for each in result.false_causes} <= {
        RULE_SIGNED_DEPLOY,
        RULE_SIGNED_DEPLOY_WEAK,
    }
    assert len(result.false_causes) <= result.windows


async def test_real_findings_are_listed_on_their_own_and_none_is_a_reported_result() -> None:
    async def reader(finding: FindingInput) -> Facts:
        return facts(finding.service or "ad")

    none = await run_healthy(reader, windows(2))
    real = RealFinding("ad", START + timedelta(days=1), None, cause=None)
    some = await run_healthy(reader, windows(2, (real,)), real=(real,))

    assert "a reported result, not a failure" in render(none)
    assert some.real_findings == (real,) and "| ad |" in render(some)


async def test_a_window_with_a_failed_read_is_counted_never_read_as_none() -> None:
    async def reader(finding: FindingInput) -> Facts:
        built = facts(finding.service or "ad")
        return Facts(
            finding=built.finding,
            now=built.now,
            zone=ZONE,
            failed_reads=frozenset({"deploys"}),
        )

    result = await run_healthy(reader, windows(2))

    assert result.windows_with_failed_reads == result.windows == 6
