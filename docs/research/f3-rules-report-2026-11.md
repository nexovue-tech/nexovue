# F3 rules-only report: how often the rules name the right cause

**Not yet run: waits for a machine that stays on (foundation item 9, Oracle after Phase 2).**
This page is replaced, whole, by the report `tools/ai-eval/bed_report.py` writes when the run
exists (plan T2.17). Demo results on synthetic data, never a customer's. No number below is a result: there is none yet.

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
rule. The deploy-to-fault link is staged, so a right score here proves the rule fires, not that it is accurate.

The bar (approved 2026-11-02, before any tune round) is read per rule in counts: a rule stating
fewer than 5 causes is not judged and stays silent; one stating 5 or more may be wrong at most a
tenth of the time, rounded down; the healthy run states none. The founder reads this report
against it (plan T2.18) and nothing here sets `CAUSE_BAR_RECORDED`.

## What passing these tests will not prove

- That a rule is right on a real install. The bed plants the deploy before the fault, so a right
  answer shows the rule fires; a decoy shows how often it is fooled by a coincidence.
- That a model resists injection. With no free text sent, a clean result reflects absent input and
  says nothing about the model. Nothing here is "injection-proof".
- That wording is true. The checks test form (names, numbers, words), not truth; a false sentence
  in allowed words passes, and the page's label says so.
- Anything about a paid provider. None was called; a free tier may keep and read what is sent, so
  only synthetic data went.

