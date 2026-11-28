"""What a model may be sent about one version, built from its facts with no AI code (F3
design, *The model's input*; A19, AC50).

Pure: the stored facts and the finding go in, a checked `ModelFacts` and the alias book
come out. Every name becomes an alias; every fact a number in the input (`e1`). Past
`MAX_MODEL_FACTS`, or past the alias bound, the rest is left out and a `truncated`
absence stands for it (FR51). Built from Step 1 so the preview has something real to
show, and so a broken-into AI process could read nothing but this (A19).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.evidence import ABSENCE_REASONS, MAX_MODEL_FACTS, TRUNCATED, evidence_id
from nexovue_platform.evidence.aliases import AliasBook, AliasesFull
from nexovue_platform.evidence.model_facts import (
    Alias,
    CalleeKind,
    Cause,
    CheckId,
    FindingFacts,
    ModelFact,
    ModelFacts,
    NextCheck,
    NextCheckKind,
)

_UNITS: Final = {"slower": "ms", "failing": "percent", "traffic": "requests"}
_CALLEE_KINDS: Final[dict[str, CalleeKind]] = {
    "postgresql": "database",
    "mysql": "database",
    "mariadb": "database",
    "mongodb": "database",
    "microsoft.sql_server": "database",
    "redis": "cache",
    "memcached": "cache",
    "valkey": "cache",
    "kafka": "queue",
    "rabbitmq": "queue",
    "aws_sqs": "queue",
}
_CHECK_STATES: Final = ("up", "down", "degraded")
# The next checks offered, in this order (FR42's list); a model may only pick by id.
_NEXT_CHECKS: Final[tuple[NextCheckKind, ...]] = (
    "open_traces",
    "open_logs",
    "open_errors",
    "open_deploys",
    "open_infrastructure",
)


def build(
    *,
    finding: Mapping[str, Any],
    items: Sequence[dict[str, Any]],
    likely_cause: Mapping[str, Any] | None,
    book: AliasBook,
    now: datetime,
) -> ModelFacts:
    """The version's model input. `book` is the previous version's, appended to."""
    service = finding.get("service_name")
    subject = book.alias("service", service) if service else None
    signal = _first_signal(finding.get("signals") or [])
    facts: list[ModelFact] = []
    cut = False
    for item in items:
        if len(facts) >= MAX_MODEL_FACTS - 1:
            cut = True
            break
        try:
            fact = _fact(item, book, finding["started_at"])
        except AliasesFull:
            cut = True
            continue
        if fact is not None:
            facts.append(fact)
    if cut:
        facts.append(
            ModelFact(
                id=book.fact(evidence_id("absence", TRUNCATED)),
                kind="absence",
                basis=None,
                absence="truncated",
            )
        )
    cause = None
    if likely_cause is not None:
        known = {book.evidence_of(fact.id): fact.id for fact in facts}
        fact_id = known.get(likely_cause["evidence_id"])
        if fact_id is not None:
            cause = Cause(evidence_id=fact_id, strength=likely_cause["strength"])
    return ModelFacts(
        finding=FindingFacts(
            subject=subject,
            signal=signal.get("signal", "slower"),
            severity=finding.get("severity", "warning"),
            unit=_UNITS[signal.get("signal", "slower")],  # type: ignore[arg-type]
            normal=_float(signal.get("normal")),
            worst=_float(signal.get("worst")),
            started_minutes_ago=max(0, int((now - finding["started_at"]) // timedelta(minutes=1))),
        ),
        facts=tuple(facts),
        likely_cause=cause,
        next_checks=tuple(
            NextCheck(
                id=CheckId(f"c{number}"),
                kind=kind,
                subject=subject if kind in ("open_traces", "open_logs", "open_errors") else None,
            )
            for number, kind in enumerate(_NEXT_CHECKS, start=1)
        ),
    )


def _callee_kind(technology: str) -> CalleeKind | None:
    """A closed label code derives from the peer's technology (design, `callee_kind`)."""
    if not technology:
        return None
    return _CALLEE_KINDS.get(technology, "other")


def _first_signal(signals: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return signals[0] if signals else {}


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _minutes_from(at: str | None, start: datetime) -> int | None:
    if at is None:
        return None
    return int((datetime.fromisoformat(at) - start) // timedelta(minutes=1))


def _fact(item: Mapping[str, Any], book: AliasBook, start: datetime) -> ModelFact | None:
    kind = item["kind"]
    values = item["values"]
    fact_id = book.fact(item["id"])
    basis = item["basis"]
    if kind == "deploy":
        return ModelFact(
            id=fact_id,
            kind="deploy",
            basis=basis,
            subject=book.alias("service", values["service"]),
            version=book.alias("version", values["version"]),
            minutes_from_start=_minutes_from(values.get("at"), start),
            deploys_last_7_days=values.get("deploys_last_7_days"),
            days_of_records=values.get("days_of_records"),
            chance=values.get("chance"),
        )
    if kind == "dependency":
        before, during = values.get("failure_share_before"), values.get("failure_share_during")
        return ModelFact(
            id=fact_id,
            kind="dependency",
            basis=basis,
            subject=book.alias("service", values["caller"]) if values.get("caller") else None,
            callee=book.alias("service", values["callee"]),
            callee_kind=_callee_kind(values.get("callee_kind", "")),
            measure="failure_share",
            before=before,
            during=during,
        )
    if kind == "other_finding":
        return ModelFact(
            id=fact_id,
            kind="other_finding",
            basis=basis,
            subject=book.alias("service", values["service"]) if values.get("service") else None,
            minutes_from_start=_minutes_from(values.get("started_at"), start),
        )
    if kind == "traffic":
        return ModelFact(
            id=fact_id,
            kind="traffic",
            basis=basis,
            subject=book.alias("service", values["service"]),
            measure="requests_per_minute",
            before=values.get("before_per_minute"),
            during=values.get("lead_per_minute"),
        )
    if kind == "error_group":
        named = values.get("exception_type")
        return ModelFact(
            id=fact_id,
            kind="error_group",
            basis=basis,
            error=book.alias("error", named or values["group_id"]),
            measure="count",
            during=float(values["count"]),
            minutes_from_start=_minutes_from(values.get("first_seen"), start),
        )
    if kind == "check":
        state = values.get("state")
        return ModelFact(
            id=fact_id,
            kind="check",
            basis=basis,
            check=book.alias("check", values["name"]),
            check_state=state if state in _CHECK_STATES else None,
            minutes_from_start=_minutes_from(values.get("at"), start),
        )
    if kind == "incident":
        return ModelFact(
            id=fact_id,
            kind="incident",
            basis=basis,
            minutes_from_start=_minutes_from(values.get("opened_at"), start),
        )
    if kind == "infrastructure":
        if values.get("measure") not in ("disk", "memory"):
            return None
        return ModelFact(
            id=fact_id,
            kind="infrastructure",
            basis=basis,
            measure="utilization_percent",
            during=float(values["utilization"]) * 100,
            minutes_from_start=_minutes_from(values.get("at"), start),
        )
    if kind == "absence" and values.get("reason") in ABSENCE_REASONS:
        return ModelFact(id=fact_id, kind="absence", basis=None, absence=values["reason"])
    return None


__all__ = ["Alias", "build"]
