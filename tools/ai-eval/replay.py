"""Replay stored facts through `rules.decide` with other constants (plan T2.15 and T2.17).

A re-run of the bed costs about 4.4 days, so tuning replays what the run stored. **Tuning sees tune
rounds only**: `sweep` filters to them itself, and the report reads report rounds with the constants
as they stood when tuning ended. Only the named constants below can be changed, each by name, and
only for the length of one replay: nothing here edits `evidence/rules.py`. The values that win are
copied there by hand, in a reviewed change (plan T2.17).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Final

from score import Counts, Decision, RoundPlan, by_rule, in_rounds, score_all

from nexovue_scheduler.evidence import rules

# The constants T2.17 tunes, by the name `rules.py` gives them. Each is a guess there (FR8).
TUNABLE: Final = (
    "CHANCE_LIKELY_BELOW",
    "CHANCE_POSSIBLE_BELOW",
    "BASE_RATE_MIN_DAYS",
    "ERROR_LEAD_IN",
    "EDGE_MIN_CALLS",
    "EDGE_FAILURE_RISE",
    "EDGE_FAILURE_RISE_POINTS",
    "EDGE_LATENCY_RISE",
    "TRAFFIC_RISE",
    "PROJECT_DEPLOY_SHARE",
)


@contextmanager
def constants(overrides: Mapping[str, Any]) -> Iterator[None]:
    """`rules`'s named constants set for the block, and put back after, even on an error."""
    unknown = set(overrides) - set(TUNABLE)
    if unknown:
        raise ValueError(f"not a tunable constant: {', '.join(sorted(unknown))}")
    saved = {name: getattr(rules, name) for name in overrides}
    for name, value in overrides.items():
        if type(value) is not type(saved[name]) and not (
            isinstance(value, int | float) and isinstance(saved[name], int | float)
        ):
            raise TypeError(f"{name} is a {type(saved[name]).__name__}, not {type(value).__name__}")
        setattr(rules, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(rules, name, value)


def replay(
    decisions: Sequence[Decision],
    overrides: Mapping[str, Any] | None = None,
    stopped: frozenset[str] = frozenset(),
) -> dict[str, Counts]:
    """Per-rule counts for `decisions` under `overrides`."""
    with constants(overrides or {}):
        return by_rule(score_all(decisions, stopped))


def sweep(
    decisions: Sequence[Decision],
    plan: RoundPlan,
    name: str,
    values: Sequence[Any],
) -> dict[Any, dict[str, Counts]]:
    """One constant tried at each value on the tune rounds: what each rule would state, and how
    often that was right. The caller reads the counts and picks; nothing is chosen here."""
    tuning = in_rounds(decisions, plan, "tune")
    return {value: replay(tuning, {name: value}) for value in values}


def minutes(value: float) -> timedelta:
    """A convenience for a duration constant given in minutes."""
    return timedelta(minutes=value)
