"""What a model may be sent about a finding: types instead of filters (design, *The
model's input*; spec BR8, AC50).

Every field of every type here is a number, a minute offset from the finding's start,
`None`, a member of a `Literal` list, or an `Alias`. `__post_init__` refuses anything
else, so a new field cannot leak a name by default, and a value such as
`signals[].key` (which holds `endpoint:<name>`) has nowhere to go. Numbers and closed
labels code derives from telemetry can still be moved by an attacker; text cannot.

Built from Step 1 with no AI code, so the preview has something real to show (A19).
"""

from __future__ import annotations

import re
import types
import typing
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Final, Literal, get_args, get_origin, get_type_hints

# Version 1 of what may be sent; a higher one ends an owner's consent (FR26).
SCHEMA_VERSION: Final = 1

_ALIAS: Final = re.compile(r"^(service|version|error|check)_[1-9][0-9]{0,3}$")
_FACT_ID: Final = re.compile(r"^e[1-9][0-9]{0,3}$")
_CHECK_ID: Final = re.compile(r"^c[1-9][0-9]?$")


class Alias(str):
    """A stand-in such as `service_1`, never a real name."""

    __slots__ = ()

    def __new__(cls, value: str) -> Alias:
        if not isinstance(value, str) or _ALIAS.fullmatch(value) is None:
            raise ValueError("an alias is service_N, version_N, error_N or check_N")
        return super().__new__(cls, value)


class FactId(str):
    """A fact's number in one input, `e1`, mapped to its evidence id apart."""

    __slots__ = ()

    def __new__(cls, value: str) -> FactId:
        if not isinstance(value, str) or _FACT_ID.fullmatch(value) is None:
            raise ValueError("a fact id is eN")
        return super().__new__(cls, value)


class CheckId(str):
    """A next check's number in one input, `c1`."""

    __slots__ = ()

    def __new__(cls, value: str) -> CheckId:
        if not isinstance(value, str) or _CHECK_ID.fullmatch(value) is None:
            raise ValueError("a next check's id is cN")
        return super().__new__(cls, value)


Signal = Literal["slower", "failing", "traffic"]
Severity = Literal["warning", "critical"]
Unit = Literal["ms", "percent", "requests"]
FactKind = Literal[
    "deploy",
    "error_group",
    "dependency",
    "check",
    "incident",
    "other_finding",
    "traffic",
    "infrastructure",
    "absence",
]
Basis = Literal["verified", "telemetry", "telemetry_text"]
CalleeKind = Literal["database", "cache", "queue", "other"]
Measure = Literal[
    "failure_share", "latency_p95_ms", "requests_per_minute", "utilization_percent", "count"
]
CheckState = Literal["up", "down", "degraded"]
AbsenceReason = Literal[
    "no_deploy_information",
    "no_dependency_traces",
    "no_host_names",
    "could_not_read",
    "truncated",
]
Strength = Literal["likely", "possible"]
NextCheckKind = Literal[
    "open_traces", "open_logs", "open_errors", "open_deploys", "open_infrastructure"
]


def _check(value: Any, hint: Any, where: str) -> None:
    """Refuse any value the hint does not admit; a plain `str` is never admitted."""
    origin = get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        errors = []
        for option in get_args(hint):
            try:
                _check(value, option, where)
                return
            except (TypeError, ValueError) as refused:
                errors.append(str(refused))
        raise TypeError(f"{where}: {value!r} fits none of {hint}")
    if hint is type(None):
        if value is not None:
            raise TypeError(f"{where}: expected None")
        return
    if origin is Literal:
        if not isinstance(value, str) or value not in get_args(hint):
            raise TypeError(f"{where}: {value!r} is not in its closed list")
        return
    if origin in (tuple, list):
        if not isinstance(value, (tuple, list)):
            raise TypeError(f"{where}: expected a sequence")
        (item,) = get_args(hint)[:1]
        for index, entry in enumerate(value):
            _check(entry, item, f"{where}[{index}]")
        return
    if hint in (Alias, FactId, CheckId):
        if type(value) is not hint:
            raise TypeError(f"{where}: expected {hint.__name__}, got {type(value).__name__}")
        return
    if hint is bool:
        if not isinstance(value, bool):
            raise TypeError(f"{where}: expected a boolean")
        return
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{where}: expected an integer")
        return
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{where}: expected a number")
        return
    if isinstance(hint, type) and hasattr(hint, "__dataclass_fields__"):
        if not isinstance(value, hint):
            raise TypeError(f"{where}: expected {hint.__name__}")
        return
    raise TypeError(f"{where}: {hint} is not a type a model may be sent")


class _Checked:
    """Checks every field against its annotation once built."""

    def __post_init__(self) -> None:
        hints = get_type_hints(type(self))
        for each in fields(self):  # type: ignore[arg-type]
            _check(getattr(self, each.name), hints[each.name], f"{type(self).__name__}.{each.name}")


@dataclass(frozen=True, slots=True)
class FindingFacts(_Checked):
    subject: Alias | None
    signal: Signal
    severity: Severity
    unit: Unit
    normal: float | None
    worst: float | None
    started_minutes_ago: int


@dataclass(frozen=True, slots=True)
class ModelFact(_Checked):
    """One fact. Fields a kind has no use for stay None."""

    id: FactId
    kind: FactKind
    basis: Basis | None
    subject: Alias | None = None
    callee: Alias | None = None
    callee_kind: CalleeKind | None = None
    version: Alias | None = None
    error: Alias | None = None
    check: Alias | None = None
    check_state: CheckState | None = None
    measure: Measure | None = None
    before: float | None = None
    during: float | None = None
    # Minutes from the finding's start: negative before it.
    minutes_from_start: int | None = None
    deploys_last_7_days: int | None = None
    days_of_records: int | None = None
    chance: float | None = None
    absence: AbsenceReason | None = None


@dataclass(frozen=True, slots=True)
class Cause(_Checked):
    evidence_id: FactId
    strength: Strength


@dataclass(frozen=True, slots=True)
class NextCheck(_Checked):
    id: CheckId
    kind: NextCheckKind
    subject: Alias | None = None


@dataclass(frozen=True, slots=True)
class ModelFacts(_Checked):
    finding: FindingFacts
    facts: tuple[ModelFact, ...] = ()
    likely_cause: Cause | None = None
    next_checks: tuple[NextCheck, ...] = field(default=())
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _Checked.__post_init__(self)
        known = {fact.id for fact in self.facts}
        if self.likely_cause is not None and self.likely_cause.evidence_id not in known:
            raise ValueError("the likely cause names a fact not in the input")

    def as_json(self) -> dict[str, Any]:
        """The stored and sent form: nothing but the checked values."""
        sent: dict[str, Any] = _without_empty(asdict(self))
        return sent


def _without_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_empty(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_without_empty(item) for item in value]
    return value
