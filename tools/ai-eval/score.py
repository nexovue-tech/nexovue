"""Stated-cause scoring: what the rules said on each fault, against the written answer key (F3 plan
T2.15; spec AC61, AC62, and AC97's bed metric). Pure: stored facts in, counts out, so a run recorded
on a machine that stays on is scored, and re-scored, anywhere.

An outcome is one of:

- `right`: a cause was stated, it links to data the facts hold, and it is the key's cause;
- `wrong`: a cause was stated and it is not, links to no data, or names a decoy deploy;
- `honest_miss`: a finding opened and no cause was stated. Whether a rule *could* have is the
  fault's `reach` in the key, so the report groups by it;
- `not_caught`: no finding opened, so there was nothing to explain (a detection result, F6's);
- `unkeyed`: the key has no row for this fault and service, so nothing can be scored.

**Counts, never percentages:** the sample is small and a percentage would look firmer than it is
(AC62). The bar is read per rule in counts (`bar_reading`); reading the report against it and
recording that it is met stays the founder's (plan T2.18), and nothing here sets
`CAUSE_BAR_RECORDED`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from answer_keys import REACHES, Key, key_for
from nexovue_platform.evidence import (
    RULE_DEPENDENCY_EDGE,
    RULE_DEPENDENCY_FINDING,
)

from nexovue_scheduler.evidence import rules
from nexovue_scheduler.evidence.rules import Cause, Facts

Outcome = Literal["right", "wrong", "honest_miss", "not_caught", "unkeyed"]
OUTCOMES: Final[tuple[Outcome, ...]] = ("right", "wrong", "honest_miss", "not_caught", "unkeyed")
RoundKind = Literal["tune", "report"]
Role = Literal["cause", "background", "decoy"]
Band = Literal["no_records", "low", "daily", "frequent"]
BANDS: Final[tuple[Band, ...]] = ("no_records", "low", "daily", "frequent")

# Deploy-frequency bands over the 7 days before a finding: a service deploying more than about
# daily is a "frequent deployer" (FR9). Guesses, named here so the report can say so.
DAILY_DEPLOYS_PER_WEEK: Final = 7
FREQUENT_DEPLOYS_PER_WEEK: Final = 15
_WEEK: Final = timedelta(days=7)

# The bar approved on 2026-11-02, before any tune round (`spec.md`, *Founder decisions needed*, 2;
# foundation item 11): a rule stating fewer than 5 causes is not judged and stays silent; one
# stating 5 or more may be wrong at most one tenth of the time, rounded down, so none for 5 to 9.
BAR_MIN_STATED: Final = 5
BAR_WRONG_DIVISOR: Final = 10

DEPENDENCY_RULES: Final = frozenset({RULE_DEPENDENCY_FINDING, RULE_DEPENDENCY_EDGE})


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """Which rounds tune the rules and which report. Named before the run (AC62): the log's
    first line holds it, and scoring a report round for tuning is refused."""

    tune: frozenset[int]
    report: frozenset[int]

    def __post_init__(self) -> None:
        if not self.tune or not self.report:
            raise ValueError("a run names at least one tune round and one report round")
        if self.tune & self.report:
            raise ValueError("a round is a tune round or a report round, never both")

    def kind_of(self, round_number: int) -> RoundKind:
        if round_number in self.tune:
            return "tune"
        if round_number in self.report:
            return "report"
        raise ValueError(f"round {round_number} is in neither list: name it before the run")


# The fault bed injects each fault in 5 rounds; two tune and three report are a guess the founder
# may change before the run starts (status.md, open decisions).
DEFAULT_ROUNDS: Final = RoundPlan(tune=frozenset({1, 2}), report=frozenset({3, 4, 5}))


@dataclass(frozen=True, slots=True)
class Planted:
    """A deploy the scheduler posted, and why: the cause of a fault, background traffic of the
    service's own rate, or a decoy unrelated to any fault."""

    service: str
    version: str
    at: datetime
    role: Role


@dataclass(frozen=True, slots=True)
class Decision:
    """One finding (or the lack of one) on one fault's injection, with the facts as they were."""

    fault: str
    service: str
    round: int
    finding_opened: bool
    facts: Facts | None
    planted: tuple[Planted, ...] = ()

    def __post_init__(self) -> None:
        if self.finding_opened != (self.facts is not None):
            raise ValueError("a decision holds facts exactly when a finding opened")


@dataclass(frozen=True, slots=True)
class Scored:
    decision: Decision
    outcome: Outcome
    cause: Cause | None
    band: Band
    # More than one rule could have named a different cause, or a tie in the same minute.
    tie: bool = False
    # A stated cause whose evidence id matches nothing in the facts (AC62's check).
    unlinked: bool = False
    on_decoy: bool = False


# ------------------------------------------------------------------ links to data


def evidence_ids_in(facts: Facts) -> frozenset[str]:
    """Every evidence id the facts can honestly stand behind: a claim's link to data."""
    found: set[str] = {rules.deploy_evidence_id(deploy) for deploy in facts.deploys}
    for edge in facts.edges:
        found.add(rules.dependency_evidence_id(edge.callee))
    for caller, edge in facts.project_edges:
        found.add(rules.dependency_evidence_id(edge.callee, caller))
        found.add(rules.dependency_evidence_id(edge.callee))
    for other in (*facts.dependency_findings, *facts.other_findings):
        found.add(rules.finding_evidence_id(other))
    for group in facts.error_groups:
        found.add(rules.error_evidence_id(group))
    if facts.finding.service is not None:
        found.add(rules.traffic_evidence_id(facts.finding.service))
    return frozenset(found)


def cause_links_to_data(cause: Cause, facts: Facts) -> bool:
    return cause.evidence_id in evidence_ids_in(facts)


# ------------------------------------------------------------------ bands and ties


def band_of(facts: Facts) -> Band:
    """The finding's service's deploy rate over the 7 days before it."""
    service = facts.finding.service
    history = facts.histories.get(service) if service is not None else None
    if history is None or history.records_since is None:
        return "no_records"
    start = facts.finding.started_at
    count = sum(1 for at in history.times if start - _WEEK <= at < start)
    if count >= FREQUENT_DEPLOYS_PER_WEEK:
        return "frequent"
    return "daily" if count >= DAILY_DEPLOYS_PER_WEEK else "low"


def is_tie(facts: Facts, cause: Cause | None, stopped: frozenset[str] = frozenset()) -> bool:
    """More than one rule may name a cause, with different evidence, or the winner shares its
    minute with the other finding that made it a cause (`same_minute`)."""
    if cause is None:
        return False
    naming = {
        p.evidence_id
        for p in rules.passed_rules(facts)
        if p.strength is not None and p.rule not in stopped
    }
    return len(naming) > 1 or cause.same_minute


# ------------------------------------------------------------------ scoring


def _planted_ids(decision: Decision, facts: Facts, role: Role) -> frozenset[str]:
    wanted = {(p.service, p.version) for p in decision.planted if p.role == role}
    return frozenset(
        rules.deploy_evidence_id(d) for d in facts.deploys if (d.service, d.version) in wanted
    )


def _right(key: Key, cause: Cause, decision: Decision, facts: Facts) -> bool:
    causes = _planted_ids(decision, facts, "cause")
    if key.truth == "deploy":
        return cause.evidence_id in causes
    if key.truth == "dependency":
        assert key.callee is not None
        named = (
            cause.rule in DEPENDENCY_RULES
            and cause.evidence_id == rules.dependency_evidence_id(key.callee)
        )
        # The callee's own staged bad release is the true cause one hop away.
        deploy_of_callee = {
            rules.deploy_evidence_id(d) for d in facts.deploys if d.service == key.callee
        } & causes
        return named or cause.evidence_id in deploy_of_callee
    return False


def decide(decision: Decision, stopped: frozenset[str] = frozenset()) -> Cause | None:
    """What the rules say now: replay's one call to `rules.decide`."""
    if decision.facts is None:
        return None
    return rules.decide(decision.facts, stopped_rules=stopped)


def score_decision(decision: Decision, cause: Cause | None) -> Scored:
    """`cause` is what `rules.decide` returned for the decision's facts, or an earlier run's."""
    facts = decision.facts
    band: Band = "no_records" if facts is None else band_of(facts)
    key = key_for(decision.fault, decision.service)
    if key is None:
        return Scored(decision, "unkeyed", cause, band)
    if facts is None:
        return Scored(decision, "not_caught", None, band)
    if cause is None:
        return Scored(decision, "honest_miss", None, band)
    tie = is_tie(facts, cause)
    linked = cause_links_to_data(cause, facts)
    on_decoy = cause.evidence_id in _planted_ids(decision, facts, "decoy")
    right = linked and _right(key, cause, decision, facts)
    return Scored(
        decision,
        "right" if right else "wrong",
        cause,
        band,
        tie=tie,
        unlinked=not linked,
        on_decoy=on_decoy,
    )


def score_all(decisions: Iterable[Decision], stopped: frozenset[str] = frozenset()) -> list[Scored]:
    return [score_decision(each, decide(each, stopped)) for each in decisions]


def in_rounds(decisions: Iterable[Decision], plan: RoundPlan, kind: RoundKind) -> list[Decision]:
    """Only the decisions of the named kind of round: tuning never sees a report round."""
    return [each for each in decisions if plan.kind_of(each.round) == kind]


# ------------------------------------------------------------------ counts


@dataclass(slots=True)
class Counts:
    findings: int = 0
    right: int = 0
    wrong: int = 0
    honest_miss: int = 0
    not_caught: int = 0
    unkeyed: int = 0
    ties: int = 0
    unlinked: int = 0
    on_decoy: int = 0

    @property
    def stated(self) -> int:
        return self.right + self.wrong

    def add(self, scored: Scored) -> None:
        self.findings += 1
        setattr(self, scored.outcome, getattr(self, scored.outcome) + 1)
        self.ties += scored.tie
        self.unlinked += scored.unlinked
        self.on_decoy += scored.on_decoy


def count_by[Group](
    scored: Iterable[Scored], group: Callable[[Scored], Group]
) -> dict[Group, Counts]:
    counts: dict[Group, Counts] = defaultdict(Counts)
    for each in scored:
        counts[group(each)].add(each)
    return dict(counts)


def by_fault(scored: Iterable[Scored]) -> dict[str, Counts]:
    return count_by(scored, lambda each: each.decision.fault)


def by_reach(scored: Iterable[Scored]) -> dict[str, Counts]:
    """Faults grouped by whether the rules could see the cause at all (AC62). An unkeyed
    decision has no reach and sits under `unkeyed`."""

    def reach(each: Scored) -> str:
        key = key_for(each.decision.fault, each.decision.service)
        return "unkeyed" if key is None else key.reach

    return count_by(scored, reach)


def by_rule(scored: Iterable[Scored]) -> dict[str, Counts]:
    """Per rule: how often it stated a cause, and how often that was right. Findings with no
    stated cause sit under the empty name."""
    return count_by(scored, lambda each: each.cause.rule if each.cause else "")


def by_rule_and_band(scored: Iterable[Scored]) -> dict[tuple[str, Band], Counts]:
    return count_by(scored, lambda each: (each.cause.rule if each.cause else "", each.band))


BarReading = Literal["silent", "met", "missed"]


def bar_reading(stated: int, wrong: int) -> BarReading:
    """The approved bar in counts, for one rule: silent below 5 stated, else wrong at most a
    tenth of stated, rounded down. For the founder to read; it records nothing."""
    if stated < BAR_MIN_STATED:
        return "silent"
    return "met" if wrong <= stated // BAR_WRONG_DIVISOR else "missed"


def rule_readings(
    scored: Iterable[Scored], forged_wrong: Mapping[str, int] | None = None
) -> dict[str, tuple[int, int, BarReading]]:
    """Per rule: stated, wrong (forged false causes added, A82), and the bar's reading."""
    extra = dict(forged_wrong or {})
    readings: dict[str, tuple[int, int, BarReading]] = {}
    for rule, counts in by_rule(scored).items():
        if not rule:
            continue
        wrong = counts.wrong + extra.pop(rule, 0)
        stated = counts.stated
        readings[rule] = (stated, wrong, bar_reading(stated, wrong))
    for rule, forged in extra.items():
        readings[rule] = (forged, forged, bar_reading(forged, forged))
    return readings


# ------------------------------------------------------------------ time to first explanation


@dataclass(frozen=True, slots=True)
class Latency:
    count: int
    missing: int
    p50: timedelta | None
    p95: timedelta | None


def _nearest_rank(sorted_values: Sequence[timedelta], share: float) -> timedelta:
    rank = max(1, math.ceil(share * len(sorted_values)))
    return sorted_values[rank - 1]


def time_to_first_explanation(
    samples: Iterable[tuple[str, datetime, datetime | None]],
) -> dict[str, Latency]:
    """AC97's bed metric: per fault, the 50th and 95th percentile of `findings.started_at` to the
    first explanation. A finding never explained is counted as missing, never dropped."""
    waits: dict[str, list[timedelta]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    for fault, started_at, explained_at in samples:
        if explained_at is None:
            missing[fault] += 1
            waits.setdefault(fault, [])
        else:
            waits[fault].append(explained_at - started_at)
    result: dict[str, Latency] = {}
    for fault, values in waits.items():
        ordered = sorted(values)
        result[fault] = Latency(
            count=len(ordered),
            missing=missing[fault],
            p50=_nearest_rank(ordered, 0.5) if ordered else None,
            p95=_nearest_rank(ordered, 0.95) if ordered else None,
        )
    return result


# ------------------------------------------------------------------ the report's tables

WARNING: Final = (
    "The deploy-to-fault link is staged, so a right score here proves the rule fires, not that "
    "it is accurate."
)


def _row(label: str, counts: Counts) -> str:
    return (
        f"| {label} | {counts.findings} | {counts.right} | {counts.wrong} | "
        f"{counts.honest_miss} | {counts.not_caught} | {counts.ties} | {counts.on_decoy} |"
    )


_HEADER: Final = (
    "| {label} | Findings | Right | Wrong | Honest miss | Not caught | Ties | On a decoy |\n"
    "|---|---|---|---|---|---|---|---|"
)


def render_tables(scored: Sequence[Scored]) -> str:
    """Markdown counts: per fault, per reach, per rule, per rule and band. No percentages."""
    out: list[str] = []
    faults = by_fault(scored)
    out += ["### Per fault", "", _HEADER.format(label="Fault")]
    out += [_row(name, faults[name]) for name in sorted(faults)]
    reaches = by_reach(scored)
    out += ["", "### Per group: could the rules see the cause at all?", ""]
    out += [_HEADER.format(label="Group")]
    out += [_row(name, reaches[name]) for name in (*REACHES, "unkeyed") if name in reaches]
    rules_counts = by_rule(scored)
    out += ["", "### Per rule", "", _HEADER.format(label="Rule")]
    out += [_row(name or "(no cause stated)", rules_counts[name]) for name in sorted(rules_counts)]
    banded = by_rule_and_band(scored)
    out += ["", "### Per rule and deploy-frequency band", ""]
    out += [_HEADER.format(label="Rule, band")]
    out += [
        _row(f"{rule or '(no cause stated)'}, {band}", banded[(rule, band)])
        for rule, band in sorted(banded, key=lambda each: (each[0], BANDS.index(each[1])))
    ]
    return "\n".join(out)
