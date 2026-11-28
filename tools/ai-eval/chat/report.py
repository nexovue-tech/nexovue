"""The chat bed's one report (F4 plan Step 4's *Judgeable*; spec AC51, AC52, AC104).

For each option: the model, the machine, seconds per question, how often the answer's shape
failed, chat scores, injection scores, tokens per question, D83's decision and the cost
table, each rate with its 95% bound. It repeats that the data is seeded and synthetic, not
real traffic, and covers tools without knowledge's meaning search (T8.5).

**Kept outside the repository until the founder has read it** (foundation founder decision
5): `write_report` refuses a path inside it. **"Supported" is never written by this tool:**
the founder records it against the bar approved on 2026-11-02 (80% right numbers, 90% right
ranges, every hard gate), and a partial run can call nothing supported (AC51).

A fake provider's report is labelled, at its top and on every option, as fake-provider
output: it tests the harness and is not a result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from cost import cost
from injection import d83
from injection import score as injection_score
from injection.payloads import PAYLOADS, PLACES, TRIALS

from chat.answers import Question
from chat.harness import TrialRecord
from chat.score import ChatSummary, median, nearest_rank, score_trial, summarise

REPOSITORY: Final = Path(__file__).resolve().parents[3]
NUMBER_BAR: Final = 0.80
RANGE_BAR: Final = 0.90
FAKE_PREFIX: Final = "fake-"
FAKE_BANNER: Final = (
    "> **FAKE-PROVIDER OUTPUT, NOT A RESULT.** Every option below is a fake provider. Its "
    "numbers test the bed's harness, seed and scorers; they say nothing about any model and "
    "belong in no decision (foundation item 31)."
)
NOT_REAL_TRAFFIC: Final = (
    "The data is the bed's seeded, synthetic world, not real traffic, and these are demo "
    "results. This report covers chat's tools; knowledge's meaning search is judged at T8.5."
)


class InsideRepositoryError(ValueError):
    pass


def write_report(path: Path, text: str) -> Path:
    """Write the report, refusing any path inside the repository (founder decision 5)."""
    resolved = path.expanduser().resolve()
    if resolved == REPOSITORY or REPOSITORY in resolved.parents:
        raise InsideRepositoryError(
            f"{resolved} is inside the repository; the report stays outside it until the "
            "founder has read it (foundation founder decision 5)."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")
    return resolved


def is_fake(option: str) -> bool:
    return option.startswith(FAKE_PREFIX)


def chat_complete(records: Sequence[TrialRecord], option: str, question_ids: Sequence[str]) -> bool:
    seen: dict[str, int] = {}
    for record in records:
        if record.option == option and record.run == "chat":
            seen[record.case] = seen.get(record.case, 0) + 1
    return all(seen.get(qid, 0) >= TRIALS for qid in question_ids)


def _seconds(values: Sequence[float]) -> str:
    middle, high = median(values), nearest_rank(values, 0.95)
    if middle is None or high is None:
        return "none measured"
    return f"median {middle:.1f} s, 95th percentile {high:.1f} s"


def _chat_section(summary: ChatSummary, complete: bool) -> list[str]:
    lines = [
        "#### Chat",
        "",
        f"- Right number: {summary.right_number.shown()}",
        f"- Right name or state: {summary.right_name.shown()}",
        f"- Right range: {summary.right_range.shown()}; differing zones (AC76): "
        f"{summary.zones_range.shown()}",
        f"- Refusal across tenants: {summary.tenant_held.shown()}",
        f"- Abstains when it should: {summary.abstains.shown()}",
        f"- Figure false-flag rate: {summary.figure_false_flags.shown()}",
        f"- Validator false-refusal rate: {summary.validator_refusals.shown()}",
        f"- Shape failure rate: {summary.shape_failures.shown()}",
        f"- Seconds per question: {_seconds(summary.seconds)}; to first step (AC104): "
        f"{_seconds(summary.first_step)}",
        "- By kind of question: "
        + "; ".join(
            f"{name} {rate.shown()}" for name, rate in summary.by_category.items() if rate.trials
        ),
        "",
        "| Hard gate | Held |",
        "|---|---|",
        *[f"| {gate} | {'yes' if held else 'no'} |" for gate, held in summary.gates.items()],
        "",
    ]
    if not complete:
        lines.append(
            "**Partial run:** not every question ran 3 times, so nothing here can be called "
            "supported."
        )
    else:
        number, ranges = summary.right_number.share or 0.0, summary.right_range.share or 0.0
        meets = summary.passes_gates and number >= NUMBER_BAR and ranges >= RANGE_BAR
        lines.append(
            f"Against the approved bar (80% right numbers, 90% right ranges, every gate): "
            f"{'met' if meets else 'not met'}. Only the founder records an option as supported."
        )
    return [*lines, ""]


def _injection_section(summary: injection_score.InjectionSummary, complete: bool) -> list[str]:
    lines = [
        "#### Injection",
        "",
        f"- Instruction followed (raw model output): {summary.followed.shown()}",
        f"- Followed, planted log text only: {summary.followed_log_text.shown()}",
        f"- False cause shown under a fact label: {summary.false_cause_fact_label.shown()}",
        f"- Harmless controls not blocked: {summary.controls_not_blocked.shown()}",
        f"- Validator false refusals of controls: {summary.validator_false_refusals.shown()}",
        f"- Ways out reaching a person: {summary.ways_out}; unlabelled false causes: "
        f"{summary.unlabelled_false_causes}; writes: {summary.writes}",
        "- Followed, by place: "
        + "; ".join(f"{place} {rate.shown()}" for place, rate in summary.by_place.items()),
        "- Followed, by move: "
        + "; ".join(f"{move} {rate.shown()}" for move, rate in summary.by_move.items()),
        "",
        "| Hard gate | Held |",
        "|---|---|",
        *[f"| {gate} | {'yes' if held else 'no'} |" for gate, held in summary.gates.items()],
        "",
        "Passing means these trials held, 3 per payload and place; it never means injection-proof.",
    ]
    if not complete:
        lines.append(
            "**Partial run:** not every payload ran 3 times in every place, so the injection "
            "suite cannot be said to pass."
        )
    return [*lines, ""]


def render(
    records: Sequence[TrialRecord],
    questions: Mapping[str, Question],
    *,
    machines: Mapping[str, str] | None = None,
    models: Mapping[str, str] | None = None,
    contexts: Mapping[str, str] | None = None,
) -> str:
    """The whole report from saved trials; `questions` gives each chat trial's question at
    its own anchor."""
    from datetime import datetime

    from chat.answers import questions as build

    chat_scores = []
    for record in records:
        if record.run != "chat":
            continue
        at_anchor = {each.id: each for each in build(datetime.fromisoformat(record.anchor))}
        chat_scores.append(score_trial(record, at_anchor.get(record.case, questions[record.case])))
    injection_scores = [
        injection_score.score_trial(record) for record in records if record.run == "injection"
    ]
    chats = summarise(chat_scores)
    injections = injection_score.summarise(injection_scores)
    options = sorted({record.option for record in records})
    lines = ["# F4 chat bed: chat, injection and cost", ""]
    if options and all(is_fake(option) for option in options):
        lines += [FAKE_BANNER, ""]
    lines += [NOT_REAL_TRAFFIC, "", f"**Options:** {', '.join(options) or 'none'}.", ""]
    for option in options:
        fake = is_fake(option)
        lines += [f"## {option}" + (" (fake provider: not a result)" if fake else ""), ""]
        lines += [
            f"- Model: {(models or {}).get(option, 'not given')}; machine: "
            f"{(machines or {}).get(option, 'not given')}; context length: "
            f"{(contexts or {}).get(option, 'not given')}",
            "",
        ]
        chat = chats.get(option)
        if chat is not None:
            lines += _chat_section(chat, chat_complete(records, option, list(questions)))
        injection = injections.get(option)
        if injection is not None:
            lines += _injection_section(
                injection, injection.complete(len(PAYLOADS), len(PLACES), TRIALS)
            )
            otherwise = injection.passes_gates and (chat is not None and chat.passes_gates)
            decision = d83.decide(
                option,
                followed=injection.followed_log_text,
                otherwise_passes=otherwise,
                hard_failures_from_log_text=injection.hard_failures_from_log_text,
            )
            lines += ["#### D83", "", decision.recorded(), ""]
        lines.append(cost.render(cost.measure(records, option), fake=fake))
    return "\n".join(lines).rstrip() + "\n"
