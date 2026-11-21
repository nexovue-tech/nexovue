"""The two reports under `docs/research/` (plan T2.17, T6.3; AC61): the rules-only report, and the
bed's report on models. Each has a skeleton saying exactly what it waits for, and a filled form for
when the data exists. **Neither skeleton holds a number.** The checked-in file is what
`skeleton_*` renders; `test_bed_report.py` fails while a file is out of date.

    uv run python tools/ai-eval/bed_report.py       # rewrites both skeletons
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import forged as forged_module
import healthy as healthy_module
import model_scoring as scoring
from fixtures.cases import ModelCase
from score import WARNING, Scored, render_tables, rule_readings

DOCS: Final = Path(__file__).resolve().parents[2] / "docs" / "research"
RULES_REPORT: Final = DOCS / "f3-rules-report-2026-11.md"
BED_REPORT: Final = DOCS / "f3-bed-report-2026-11.md"

LABEL: Final = "Demo results on synthetic data, never a customer's."
NOT_PASSING_PROVES: Final = """\
## What passing these tests will not prove

- That a rule is right on a real install. The bed plants the deploy before the fault, so a right
  answer shows the rule fires; a decoy shows how often it is fooled by a coincidence.
- That a model resists injection. With no free text sent, a clean result reflects absent input and
  says nothing about the model. Nothing here is "injection-proof".
- That wording is true. The checks test form (names, numbers, words), not truth; a false sentence
  in allowed words passes, and the page's label says so.
- Anything about a paid provider. None was called; a free tier may keep and read what is sent, so
  only synthetic data went."""


def skeleton_rules() -> str:
    return f"""\
# F3 rules-only report: how often the rules name the right cause

**Not yet run: waits for a machine that stays on (foundation item 9, Oracle after Phase 2).**
This page is replaced, whole, by the report `tools/ai-eval/bed_report.py` writes when the run
exists (plan T2.17). {LABEL} No number below is a result: there is none yet.

## What the run needs

- **The fault bed's run**, `tools/fault-bed/README.md`: 3 weeks of seeded history, 1 week of the
  live demo, a 7-day reference run, then about 4.4 days of injections: about 11.5 days in all.
- **The deploy scheduler** (`tools/ai-eval/deploy_scheduler.py`), posting signed deploys through
  the real webhook for at least 7 days, at varied rates per service, with decoys. It must start
  with the warm-up so every service has a deploy history before the first injection.
- **The facts log** (`tools/ai-eval/facts_log.py`): the tune and report rounds named on its
  first line before the run starts, and every finding's facts stored as read.
- **The healthy run's windows** (`tools/ai-eval/healthy.py`) over the reference week, and the
  forged run (`tools/ai-eval/forged.py`, `--run forged`) on the same stack.

## What it will say

Counts, never percentages, because the sample is small: per fault, per group (could the rules
see the cause at all), per rule, and per rule and deploy-frequency band; every tie; every cause
stated on a decoy deploy; the healthy windows with the count of windows; the forged causes by
rule. {WARNING}

The bar (approved 2026-11-02, before any tune round) is read per rule in counts: a rule stating
fewer than 5 causes is not judged and stays silent; one stating 5 or more may be wrong at most a
tenth of the time, rounded down; the healthy run states none. The founder reads this report
against it (plan T2.18) and nothing here sets `CAUSE_BAR_RECORDED`.

{NOT_PASSING_PROVES}

"""


def skeleton_bed() -> str:
    return f"""\
# F3 bed on models: injection, cost and wording

**Not yet run: the founder's, after Phase 2** (foundation item 31: every real-model test is the
founder's after Phase 2). This page is replaced, whole, by the report
`tools/ai-eval/bed_report.py` writes when the real runs exist (plan T6.3). {LABEL} No number
below is a result: there is none yet.

## What was built and tested, and what was not

The harness (`tools/ai-eval/model_runs.py`, `model_scoring.py`, `fixtures/`) records each
`model_input` once, replays it across providers offline, and scores injection, wording and cost.
It was tested against **fake providers only**: they test the harness, not any model, and no
figure from one belongs in a decision. No real provider was called.

## What the real run needs

- A free-tier key under the founder's own account (Gemini's is the likely one), synthetic data
  only.
- The local model on the founder's Mac through Ollama, replaying the recorded inputs with the
  dev stack stopped, models up to about 14B (A81). The report names the model and the machine.
- The 42 payloads of LogJack, replacing this build's stand-ins in
  `tools/ai-eval/fixtures/payloads.py` (the paper could not be fetched when the stand-ins were
  written).
- Repeated trials per input (a guess: 10), for each option.

## What it will say

Per named model: trials, ways out attempted before validation and any that survived it (a
survivor fails the model); the ordinary inputs' claims shown and removed, by rule; abstentions
and unusable answers; tokens per call and, only where a price was given, cost. It repeats *What
passing these tests will not prove*, and says paid providers are not yet tested.

{NOT_PASSING_PROVES}

"""


def render_rules_report(
    scored: Sequence[Scored],
    healthy: healthy_module.HealthyResult,
    forged: forged_module.ForgedReport,
) -> str:
    """The filled rules-only report from report-round decisions; `scored` holds those only."""
    forged_wrong = forged.false_by_rule
    readings = rule_readings(scored, forged_wrong)
    bar_lines = ["| Rule | Stated | Wrong (with forged) | Bar |", "|---|---|---|---|"]
    bar_lines += [
        f"| {rule} | {stated} | {wrong} | {reading} |"
        for rule, (stated, wrong, reading) in sorted(readings.items())
    ]
    healthy_reading = "met" if healthy.clean else "missed"
    return "\n".join(
        [
            "# F3 rules-only report",
            "",
            f"{LABEL} {WARNING}",
            "",
            "## Report rounds",
            "",
            render_tables(scored),
            "",
            "## The bar, read per rule in counts (for the founder's reading)",
            "",
            *bar_lines,
            "",
            f"Healthy run, no cause stated at all: **{healthy_reading}**.",
            "",
            "## Healthy windows",
            "",
            healthy_module.render(healthy),
            "",
            "## Forged spans through the real intake",
            "",
            forged_module.render(forged),
            "",
            NOT_PASSING_PROVES,
            "",
        ]
    )


def render_bed_report(
    scored: Sequence[tuple[scoring.TrialScore, ModelCase]],
    cost: Mapping[str, scoring.CostRow],
    *,
    machine: str,
) -> str:
    """The filled report on models. A provider named `fake-…` is labelled as a harness test."""
    injection = scoring.injection_by_provider(scored)
    wording = scoring.wording_by_provider(scored)
    lines = [
        "# F3 bed on models",
        "",
        f"{LABEL} Run on: {machine}. Paid providers are not yet tested. A free tier may keep what "
        "is sent, so only synthetic data went. No write exists to try, by construction (AC43).",
    ]
    if any(name.startswith("fake-") for name in injection):
        lines += [
            "",
            "**Providers named `fake-` are test doubles. They test this harness, not any model.**",
        ]
    lines += [
        "",
        "## Injection",
        "",
        "| Provider | Trials | Calls failed | Trials with an attempt | Survived validation |",
        "|---|---|---|---|---|",
    ]
    for name, row in sorted(injection.items()):
        survived = ", ".join(sorted(row.survived)) or "none"
        lines.append(
            f"| {name} | {row.trials} | {row.calls_failed} | "
            f"{row.trials_with_an_attempt} | {survived} |"
        )
    lines += ["", "## Wording on the ordinary inputs", ""]
    lines += ["| Provider | Trials | Written | Shown | Abstained | Unusable | Removed by |"]
    lines += ["|---|---|---|---|---|---|---|"]
    for name, wrow in sorted(wording.items()):
        removed = ", ".join(f"{rule} {count}" for rule, count in sorted(wrow.removed_by.items()))
        lines.append(
            f"| {name} | {wrow.trials} | {wrow.written} | {wrow.shown} | {wrow.abstained} | "
            f"{wrow.unusable} | {removed or 'none'} |"
        )
    lines += [
        "",
        "## Cost",
        "",
        "| Provider | Calls | Tokens in | Tokens out | Estimated | p50 | p95 | Cost |",
    ]
    lines += ["|---|---|---|---|---|---|---|---|"]
    for name, crow in sorted(cost.items()):
        price = scoring.PRICED_NOTE if crow.dollars is None else f"US${crow.dollars:.4f}"
        lines.append(
            f"| {name} | {crow.calls} | {crow.tokens_in} | {crow.tokens_out} | "
            f"{crow.estimated_calls} | {crow.p50_tokens} | {crow.p95_tokens} | {price} |"
        )
    lines += ["", NOT_PASSING_PROVES, ""]
    return "\n".join(lines)


def main() -> int:
    RULES_REPORT.write_text(skeleton_rules(), encoding="utf-8")
    BED_REPORT.write_text(skeleton_bed(), encoding="utf-8")
    print(f"wrote {RULES_REPORT.name} and {BED_REPORT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
