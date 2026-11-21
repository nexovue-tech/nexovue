"""`just explain-demo` writes what each rule reads, and says detection did not judge (F3 T1.12)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from explain_demo import DISCLAIMER, RULES, plan, sign, webhook_body

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
START = NOW - timedelta(minutes=2)


def test_every_rule_has_a_demo_and_an_unknown_one_is_refused() -> None:
    assert RULES == (1, 2, 3, 4, 5, 6, 7, 8)
    for rule in RULES:
        assert plan(rule, "checkout", now=NOW).rule == rule
    with pytest.raises(ValueError):
        plan(9, "checkout", now=NOW)


def test_rule_1_sends_a_signed_deploy_of_the_service_four_minutes_before() -> None:
    demo = plan(1, "checkout", now=NOW)

    (deploy,) = demo.deploys
    assert (deploy.service, START - deploy.at) == ("checkout", timedelta(minutes=4))
    assert not demo.edges


def test_rule_3_deploys_a_service_with_no_history() -> None:
    demo = plan(3, "checkout", now=NOW)

    assert demo.service == "checkout-new" and demo.deploys[0].service == "checkout-new"


def test_rule_2_moves_the_calls_and_opens_the_dependency_first() -> None:
    demo = plan(2, "checkout", now=NOW)

    during = [edge for edge in demo.edges if edge.minute >= START]
    before = [edge for edge in demo.edges if edge.minute < START]
    assert during and before
    assert min(edge.errors for edge in during) > max(edge.errors for edge in before)
    assert demo.dependency_finding_ago == timedelta(minutes=3)


def test_rule_4_deploys_the_dependency_and_leaves_its_calls_steady() -> None:
    demo = plan(4, "checkout", now=NOW)

    assert [deploy.service for deploy in demo.deploys] == ["payments"]
    assert len({edge.errors for edge in demo.edges}) == 1


def test_rules_5_and_8_write_what_only_supports_a_cause() -> None:
    assert plan(5, "checkout", now=NOW).seen_versions and not plan(5, "checkout", now=NOW).deploys
    eight = plan(8, "checkout", now=NOW)
    assert eight.errors and eight.edges


def test_rule_7_raises_traffic_in_the_lead_in() -> None:
    demo = plan(7, "checkout", now=NOW)

    lead = [
        requests for minute, requests in demo.traffic if minute >= START - timedelta(minutes=15)
    ]
    before = [
        requests for minute, requests in demo.traffic if minute < START - timedelta(minutes=15)
    ]
    assert min(lead) >= 1.5 * max(before)


def test_the_deploy_goes_through_the_webhook_signed_as_the_webhook_checks() -> None:
    demo = plan(1, "checkout", now=NOW)
    body = webhook_body(demo.deploys)

    item = json.loads(body)["deploys"][0]
    assert {"delivery_id", "service_name", "version", "deployed_at"} <= set(item)
    expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert sign(body, "secret") == f"sha256={expected}"


def test_it_says_plainly_that_detection_did_not_judge_the_finding() -> None:
    assert DISCLAIMER.startswith("Detection did not judge this finding")


def test_it_refuses_a_host_other_than_this_machine() -> None:
    from explain_demo import main

    with pytest.raises(SystemExit, match="refusing"):
        main(["--rule", "1", "--postgres", "postgresql://u:p@db.example.com:5432/x"])
