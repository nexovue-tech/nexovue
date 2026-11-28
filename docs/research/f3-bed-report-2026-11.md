# F3 bed on models: injection, cost and wording

**Not yet run: the founder's, after Phase 2** (foundation item 31: every real-model test is the
founder's after Phase 2). This page is replaced, whole, by the report
`tools/ai-eval/bed_report.py` writes when the real runs exist (plan T6.3). Demo results on synthetic data, never a customer's. No number
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

## What passing these tests will not prove

- That a rule is right on a real install. The bed plants the deploy before the fault, so a right
  answer shows the rule fires; a decoy shows how often it is fooled by a coincidence.
- That a model resists injection. With no free text sent, a clean result reflects absent input and
  says nothing about the model. Nothing here is "injection-proof".
- That wording is true. The checks test form (names, numbers, words), not truth; a false sentence
  in allowed words passes, and the page's label says so.
- Anything about a paid provider. None was called; a free tier may keep and read what is sent, so
  only synthetic data went.

