"""Chat's cost run: what each guessed limit would be, measured (F4 plan T4.3; spec AC52,
AC104, D26; design, *Performance and scale*).

From the bed's saved trials (chat and injection), per option: tokens, calls and steps per
question, how often a question used all its steps or had a result cut, the largest request
sent, seconds per question and to the first step. Beside each guess it names **the constant,
where it lives, its value now, and what this run measured.**

**It changes nothing.** A constant moves only after the founder's real runs (foundation item
31): a fake provider's tokens are the fake's own fixed numbers, and say nothing about cost.
Where a guess has no measurement here, it says so: how many questions a person asks a day
(FR20) is people's behaviour, not the bed's.

**The suggested monthly limit has a known gap:** `SUGGESTED_MONTHLY_TOKENS` is F3's 2 million
for explanations, while spec D26 says about 45 million with chat on (5 people, 10 questions
a working day, 22 days, chat stopping at 80%). The arithmetic is shown with this run's
tokens, labelled with what it rests on; the constant waits for the real runs.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from chat.harness import TrialRecord

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]

# D26's example team, and chat's share of the month (spec FR19, D26).
PEOPLE: Final = 5
QUESTIONS_A_DAY: Final = 10
WORKING_DAYS: Final = 22
CHAT_SHARE: Final = 0.8
D26_SUGGESTION: Final = 45_000_000


@dataclass(frozen=True, slots=True)
class Guess:
    """One guessed limit: the requirement, the constant, where it lives, its value now."""

    requirement: str
    constant: str
    path: str
    current: int
    measures: str


def guesses() -> tuple[Guess, ...]:
    """Read from the code itself, so the tool names the values the code holds today."""
    from nexovue_platform.assistant_records import (
        DEFAULT_DAILY_QUESTION_LIMIT,
        MAX_STEPS,
        QUESTION_RESERVATION_TOKENS,
    )
    from nexovue_platform.assistant_tools import CHAT_MAX_CHARACTERS

    from nexovue_api.ai.schemas import SUGGESTED_MONTHLY_TOKENS

    platform = "packages/py-platform/src/nexovue_platform"
    return (
        Guess(
            "FR10",
            "MAX_STEPS",
            f"{platform}/assistant_records.py",
            MAX_STEPS,
            "steps per question, and how often all were used",
        ),
        Guess(
            "FR11",
            "CHAT_MAX_CHARACTERS",
            f"{platform}/assistant_tools.py",
            CHAT_MAX_CHARACTERS,
            "how often a step's result was cut",
        ),
        Guess(
            "FR19",
            "QUESTION_RESERVATION_TOKENS",
            f"{platform}/assistant_records.py",
            QUESTION_RESERVATION_TOKENS,
            "tokens per question, against the reservation",
        ),
        Guess(
            "FR20",
            "DEFAULT_DAILY_QUESTION_LIMIT",
            f"{platform}/assistant_records.py",
            DEFAULT_DAILY_QUESTION_LIMIT,
            "not measured by the bed: how many questions people ask is their use",
        ),
        Guess(
            "D26",
            "SUGGESTED_MONTHLY_TOKENS",
            "apps/api/src/nexovue_api/ai/schemas.py",
            SUGGESTED_MONTHLY_TOKENS,
            "the monthly suggestion, from tokens per question (spec D26: about 45 million)",
        ),
    )


@dataclass(frozen=True, slots=True)
class Costs:
    option: str
    questions: int
    tokens: tuple[int, ...]
    calls: tuple[int, ...]
    steps: tuple[int, ...]
    estimated_calls: int
    all_steps_used: int
    steps_cut: int
    steps_read: int
    largest_request: int
    seconds: tuple[float, ...]
    first_step: tuple[float, ...]
    over_reservation: int

    def percentile(self, values: Sequence[float], share: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def measure(records: Sequence[TrialRecord], option: str) -> Costs:
    from nexovue_platform.assistant_records import MAX_STEPS, QUESTION_RESERVATION_TOKENS

    mine = [record for record in records if record.option == option]
    tokens = tuple(
        sum(
            int(call.get("input_tokens") or 0) + int(call.get("output_tokens") or 0)
            for call in record.calls
        )
        for record in mine
    )
    read_steps = [step for record in mine for step in record.steps if step["state"] == "read"]
    return Costs(
        option=option,
        questions=len(mine),
        tokens=tokens,
        calls=tuple(len(record.calls) for record in mine),
        steps=tuple(len(record.steps) for record in mine),
        estimated_calls=sum(
            1 for record in mine for call in record.calls if call.get("tokens_estimated")
        ),
        all_steps_used=sum(1 for record in mine if len(record.steps) >= MAX_STEPS),
        steps_cut=sum(1 for step in read_steps if step.get("was_cut")),
        steps_read=len(read_steps),
        largest_request=max(
            (int(call.get("request_characters") or 0) for record in mine for call in record.calls),
            default=0,
        ),
        seconds=tuple(record.seconds for record in mine),
        first_step=tuple(
            record.first_step_seconds for record in mine if record.first_step_seconds is not None
        ),
        over_reservation=sum(1 for each in tokens if each > QUESTION_RESERVATION_TOKENS),
    )


def monthly_arithmetic(tokens_per_question: float) -> int:
    """D26's arithmetic with a measured figure: the example team's month, at chat's share."""
    month = tokens_per_question * PEOPLE * QUESTIONS_A_DAY * WORKING_DAYS
    return math.ceil(month / CHAT_SHARE)


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _number(value: float | None, unit: str = "") -> str:
    if value is None:
        return "none measured"
    return f"{value:,.0f}{unit}" if value >= 10 else f"{value:.1f}{unit}"


def render(costs: Costs, *, fake: bool) -> str:
    """The cost section for one option: each guess, its constant, and what was measured."""
    median_tokens = statistics.median(costs.tokens) if costs.tokens else None
    p95 = costs.percentile(costs.tokens, 0.95)
    by_requirement = {guess.requirement: guess for guess in guesses()}
    measured = {
        "FR10": f"median {_number(statistics.median(costs.steps) if costs.steps else None)} steps; "
        f"{costs.all_steps_used} of {costs.questions} used all",
        "FR11": f"{costs.steps_cut} of {costs.steps_read} read steps cut; largest request "
        f"{costs.largest_request:,} characters",
        "FR19": f"median {_number(median_tokens)}, 95th percentile {_number(p95)}, largest "
        f"{_number(max(costs.tokens) if costs.tokens else None)} tokens; "
        f"{costs.over_reservation} above the reservation",
        "FR20": "not measured (people's use)",
        "D26": "none measured"
        if median_tokens is None
        else f"{monthly_arithmetic(median_tokens):,} tokens a month by D26's arithmetic "
        f"(median tokens per question x {PEOPLE} people x {QUESTIONS_A_DAY} a day x "
        f"{WORKING_DAYS} days / {CHAT_SHARE}), F3's use not included",
    }
    label = (
        "**Fake-provider figures: a fake's tokens are fixed numbers it reports, not a cost.**"
        if fake
        else "Measured on this option only; a different model costs differently."
    )
    lines = [
        f"### Cost: {costs.option}",
        "",
        label,
        "",
        "| Guess | Constant | Where | Now | Measured here |",
        "|---|---|---|---|---|",
    ]
    for requirement in ("FR10", "FR11", "FR19", "FR20", "D26"):
        guess = by_requirement[requirement]
        lines.append(
            f"| {requirement} | `{guess.constant}` | `{guess.path}` | {guess.current:,} | "
            f"{measured[requirement]} |"
        )
    gap = by_requirement["D26"]
    lines += [
        "",
        f"- **Named gap:** `{gap.constant}` is {gap.current:,} (F3's figure) in `{gap.path}`; "
        f"spec D26 suggests about {D26_SUGGESTION:,} with chat on. It changes only after the "
        "founder's real runs, with its test.",
        f"- Seconds per question: median {_number(_median(costs.seconds), ' s')}, "
        f"95th percentile {_number(costs.percentile(costs.seconds, 0.95), ' s')}. "
        f"Time to first step (AC104): median "
        f"{_number(_median(costs.first_step), ' s')} "
        "(NFR3's targets: about 5 s on a cloud provider, a call's length on a local model).",
        f"- Calls whose tokens the provider did not report (estimated): {costs.estimated_calls}.",
        "- **No constant was changed by this run.**",
    ]
    return "\n".join(lines) + "\n"
