"""The deploy scheduler: what it plans, what it signs, and how it posts (F3 plan T2.15; AC62).

The body is checked against the real webhook's schema and signature check, so what the scheduler
posts is what the webhook would accept. The post itself is a fake: no stack is needed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from answer_keys import KEYS, cause_version
from deploy_scheduler import (
    KEEP_CLEAR,
    LEAD,
    MIN_DAYS,
    Window,
    batch_body,
    build_plan,
    delivery_id,
    rate_of,
    run,
    sign,
)
from score import FREQUENT_DEPLOYS_PER_WEEK, Planted

from nexovue_api.deploys.schemas import DeployBatch
from nexovue_api.deploys.signatures import SIGNATURE_HEADER, matching_secret

START = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
SERVICES = ["ad", "cart", "checkout", "email", "payment", "shipping"]
SECRET = "nvdw_test-secret"


def windows() -> list[Window]:
    return [
        Window("adFailure", 1, START + timedelta(days=3)),
        Window("adHighCpu", 2, START + timedelta(days=3, hours=2)),
        Window("adHighCpu", 3, START + timedelta(days=3, hours=4)),
        Window("paymentFailure", 1, START + timedelta(days=4)),
    ]


def plan() -> list[Planted]:
    return build_plan(services=SERVICES, windows=windows(), start=START, days=8, seed=7)


def test_it_refuses_a_plan_shorter_than_the_seven_days_ac62_asks_for() -> None:
    with pytest.raises(ValueError, match=str(MIN_DAYS)):
        build_plan(services=SERVICES, windows=[], start=START, days=MIN_DAYS - 1)


def test_a_deploy_fault_gets_its_staged_cause_four_minutes_before_it_starts() -> None:
    causes = {(p.service, p.version, p.at) for p in plan() if p.role == "cause"}

    start = START + timedelta(days=3)
    assert ("ad", cause_version("adFailure", 1), start - LEAD) in causes
    # paymentFailure stages the payment deploy; checkout's key is a dependency, so no second one.
    assert sum(1 for c in causes if c[1] == cause_version("paymentFailure", 1)) == 1


def test_a_fault_no_rule_can_see_gets_a_decoy_deploy_on_every_other_round_only() -> None:
    decoys = [p for p in plan() if p.role == "decoy"]

    assert [(p.service, p.version) for p in decoys] == [("ad", "bed-decoy-adHighCpu-r2")]


def test_services_deploy_at_varied_rates_with_one_past_the_frequent_line() -> None:
    background = Counter(p.service for p in plan() if p.role == "background")

    assert len(set(background.values())) > 1
    frequent = max(background.values())
    assert frequent / 8 * 7 >= FREQUENT_DEPLOYS_PER_WEEK
    assert rate_of("ad", SERVICES) != rate_of("cart", SERVICES)


def test_no_background_deploy_lands_within_the_clear_distance_of_an_injection() -> None:
    starts = [w.start for w in windows()]

    for deploy in plan():
        if deploy.role == "background":
            assert all(abs(deploy.at - start) >= KEEP_CLEAR for start in starts)


def test_the_same_seed_gives_the_same_plan_and_another_gives_another() -> None:
    assert plan() == plan()
    other = build_plan(services=SERVICES, windows=windows(), start=START, days=8, seed=8)
    assert other != plan()


def test_what_it_posts_is_a_body_the_real_webhook_accepts_signed_the_way_it_checks() -> None:
    body = batch_body(plan()[:5])

    parsed = DeployBatch.model_validate_json(body)

    assert len(parsed.deploys) == 5
    assert matching_secret(body=body, header_value=sign(body, SECRET), secrets=[SECRET]) == 0
    assert matching_secret(body=body, header_value=sign(body, "other"), secrets=[SECRET]) is None
    assert (
        matching_secret(body=body + b" ", header_value=sign(body, SECRET), secrets=[SECRET]) is None
    )


def test_a_delivery_id_is_stable_so_a_repeat_is_counted_by_the_webhook() -> None:
    [first, *_] = plan()

    assert delivery_id(first) == delivery_id(first)
    assert delivery_id(first) != delivery_id(plan()[1])


def _post_into(sent: list[tuple[str, bytes, Mapping[str, str]]], status: int = 200):  # type: ignore[no-untyped-def]
    def post(url: str, body: bytes, headers: Mapping[str, str]) -> int:
        sent.append((url, body, headers))
        return status

    return post


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=max(seconds, 1))


def test_it_posts_each_deploy_once_when_its_time_comes_and_signs_every_batch(
    tmp_path: Path,
) -> None:
    sent: list[tuple[str, bytes, Mapping[str, str]]] = []
    clock = Clock(START)
    deploys = plan()

    posted = run(
        deploys,
        base_url="http://localhost:18000",
        project_id="p1",
        secret=SECRET,
        post=_post_into(sent),
        ledger=tmp_path / "ledger",
        now=clock.now,
        sleep=clock.sleep,
        poll_seconds=3600,
    )

    assert posted == len(deploys)
    assert all(url == "http://localhost:18000/api/v1/deploys/webhook/p1" for url, _, _ in sent)
    assert all(
        matching_secret(body=body, header_value=headers[SIGNATURE_HEADER], secrets=[SECRET]) == 0
        for _, body, headers in sent
    )
    in_bodies = sum(len(DeployBatch.model_validate_json(body).deploys) for _, body, _ in sent)
    assert in_bodies == len(deploys)


def test_a_stopped_run_resumes_without_posting_a_deploy_twice(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    deploys = plan()
    halfway = deploys[len(deploys) // 2].at
    # Simulate the stop: the first half is in the ledger, as a run that died there would leave it.
    done = [d for d in deploys if d.at <= halfway]
    ledger.write_text("".join(delivery_id(d) + "\n" for d in done), encoding="utf-8")
    second: list[tuple[str, bytes, Mapping[str, str]]] = []
    clock = Clock(START)

    posted = run(
        deploys,
        base_url="http://localhost:18000",
        project_id="p1",
        secret=SECRET,
        post=_post_into(second),
        ledger=ledger,
        now=clock.now,
        sleep=clock.sleep,
        poll_seconds=3600,
    )

    assert posted == len(deploys) - len(done)


def test_a_refused_batch_stops_the_run_loudly_and_is_not_marked_posted(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"

    with pytest.raises(RuntimeError, match="HTTP 401"):
        run(
            plan()[:3],
            base_url="http://localhost:18000",
            project_id="p1",
            secret=SECRET,
            post=_post_into([], status=401),
            ledger=ledger,
            now=lambda: START + timedelta(days=30),
            sleep=lambda _seconds: None,
        )

    assert not ledger.exists() or ledger.read_text(encoding="utf-8") == ""


def test_every_staged_key_has_a_version_the_scorer_can_find_again() -> None:
    versions = {cause_version(key.fault, 3) for key in KEYS if key.truth == "deploy"}

    assert len(versions) == sum(1 for key in KEYS if key.truth == "deploy")
