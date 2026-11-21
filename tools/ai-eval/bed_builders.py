"""Facts and decisions for the bed's tests: small builders over `rules.Facts`, so each test names
only what it is about. Test support, not shipped behaviour."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from answer_keys import cause_version
from nexovue_platform.ai.validate import Checked, Claim, Refused
from score import Decision, Planted

from nexovue_scheduler.evidence.rules import (
    DeployFact,
    DeployHistory,
    EdgeFact,
    Facts,
    FindingInput,
    OtherFinding,
)

START = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ZONE = ZoneInfo("UTC")
# Eight days of deploy records, so a deploy may be "likely" and not only "possible" (FR7).
RECORDS_SINCE = START - timedelta(days=8)


def finding(service: str = "ad", signals: tuple[str, ...] = ("slower",)) -> FindingInput:
    return FindingInput(
        id=f"f-{service}",
        scope="service",
        service=service,
        started_at=START,
        signals=signals,  # type: ignore[arg-type]
    )


def deploy(service: str, version: str, minutes_before: int = 4) -> DeployFact:
    return DeployFact(
        id=f"d-{service}-{version}",
        service=service,
        version=version,
        at=START - timedelta(minutes=minutes_before),
        signed=True,
    )


def history(service: str, deploys_in_week: int = 2) -> DeployHistory:
    """A quiet history that does not include the deploy under test, at hours that are not the
    finding's, so the base rate is low."""
    spacing = timedelta(days=7) / max(deploys_in_week, 1)
    times = tuple(START - spacing * (index + 0.5) for index in range(deploys_in_week))
    return DeployHistory(service, times, RECORDS_SINCE)


def moved_edge(callee: str) -> EdgeFact:
    return EdgeFact(
        callee=callee,
        calls_before=200,
        calls_during=200,
        failures_before=2,
        failures_during=60,
        p95_before=10.0,
        p95_during=12.0,
    )


def facts(
    service: str = "ad",
    *,
    deploys: tuple[DeployFact, ...] = (),
    edges: tuple[EdgeFact, ...] = (),
    deploys_in_week: int = 2,
    dependency_findings: tuple[OtherFinding, ...] = (),
) -> Facts:
    return Facts(
        finding=finding(service),
        now=START + timedelta(minutes=5),
        zone=ZONE,
        deploys=deploys,
        histories={service: history(service, deploys_in_week)},
        edges=edges,
        dependency_findings=dependency_findings,
    )


def decision(
    fault: str,
    service: str,
    round_number: int,
    *,
    opened: bool = True,
    built: Facts | None = None,
    planted: tuple[Planted, ...] = (),
) -> Decision:
    return Decision(
        fault=fault,
        service=service,
        round=round_number,
        finding_opened=opened,
        facts=(built if built is not None else facts(service)) if opened else None,
        planted=planted,
    )


def staged(fault: str, service: str, round_number: int) -> tuple[Facts, tuple[Planted, ...]]:
    """Facts holding the fault's staged signed deploy, and the plan that says it is the cause."""
    version = cause_version(fault, round_number)
    at = START - timedelta(minutes=4)
    return (
        facts(service, deploys=(deploy(service, version),)),
        (Planted(service, version, at, "cause"),),
    )


def passes_everything(answer: str, _model_input: object) -> Checked | Refused:
    """A broken validator that shows every claim it is given, for the tests that must see the
    injection scoring notice a way out (and fail the provider) when the validator lets one by."""
    try:
        document = json.loads(answer)
        claims = tuple(
            Claim(each["type"], each["text"], tuple(each["evidence_ids"]))
            for each in document["claims"]
        )
    except (ValueError, KeyError, TypeError):
        return Refused()
    return Checked(claims=claims, picks=(), abstained=False)
