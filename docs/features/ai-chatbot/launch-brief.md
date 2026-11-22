# Launch brief: Ask (F4)

> **DRAFT for the founder's approval (plan T8.5, AC100). Not approved. No public text uses anything below until
> it is.** Written 2026-11-22, before any real model was run. Every row marked *no proof yet* stays out of public
> text until its proof exists.

## Name and positioning

**Ask** (D52), a panel beside every console screen, with "Ask about this finding" on a finding. Positioning: an
answer you can check. Each sentence shows what was read and over what time range; it reads but has no tool that
can change anything; it runs on the AI account or model the customer chooses.

**Selling line,** approved 2026-11-02 (foundation item 2), shown on the consent step as written
(`ai-words.ts`, `SELLING_LINE`): "Your telemetry is stored on your server. AI is off until you turn it on, and you
choose where it runs: your own AI account. … Chat, if you add it, sends questions, log text, notes and runbooks
under its own consent."

**Who can get it:** self-hosted installs only. The hosted service shows no panel (`useOffersAssistant`).

## Each public claim beside its proof

| Claim | Proof | Status |
|---|---|---|
| Chat has no tool that can write | Catalog of GETs only; ticket refused on other methods (AC7, AC10); hostile suites offline and live | **Proved by tests**, run 2026-11-22 |
| nexovue checks each answer and removes model-written links and commands; that check can miss things (BR18) | Validator tests; the bed's injection report | Tests pass; **the bed has run on fake providers only** |
| Each sentence shows what was read, over what range | Citation check (`assistant_answer.py`); `assistant.spec.ts` | **Proved by tests and Playwright**, 2026-11-22 |
| Log text goes to the AI provider you choose | Consent preview (AC2) | **Proved by tests** |
| Answers right numbers / abstains when it should, on model X | The bed's report on a real option against the approved bar | **No proof yet**: no real model run |
| Works with a local model on a named machine | T8.0's local run (`gpt-oss:20b`, Apple M4 Pro, 24 GB, D80) | **No proof yet** |
| Meaning-based search | T6.6 on a named setup | **No proof yet**; keyword search is what is proved |
| Does not slow detection or alerts | AC80 on the fault bed | **No proof yet** (readiness, blocker 2) |

**Never said** (BR18, held by `test_banned_claims.py`): "no way out", "injection-proof", "proved safe", "your logs
stay on your server" to a cloud-provider user, "cheaper than" anyone.

## Providers measured, including embeddings

| Provider and model | Machine | Chat | Injection | Embeddings | Result |
|---|---|---|---|---|---|
| — | — | — | — | — | **None measured.** Rows are added from the bed's report, after Phase 2 |

## What it can read, and cannot

**Reads** (the catalog, `assistant_tools.py`, 25 tools): findings, a finding's explanation and timeline; services,
a service's detection state, endpoints and trend; the service map; releases and a release comparison; incidents;
synthetic checks; error groups; logs, traces and one trace; metrics and a metric's histogram; hosts and a host's
trend; and runbooks, case notes and resolved cases (knowledge search). Each read covers at most 7 days; at most 8
reads a question.

**Cannot read:** anything outside the asker's own projects, or another organization; settings, keys, members or
billing. **Cannot do:** write, send, create an issue, page anyone.

## A repeatable demo

1. A development install with the fault bed's demo data (`just dev`, `just detection-seed`), chat on with the
   chosen model.
2. Ask research section 2's known questions with a deliberate time range; open each step's link and show the same
   data on its screen.
3. Plant a log line holding an instruction and a link (`tools/gateseed --log-body`); ask the question that reads
   it; show the link as inert text and no step following it.

**Demo-data warning** (foundation section 11): the demo is not real traffic; anything shown or measured on it is a
demo result and says so.

## Competitor facts: to recheck within 30 days before release

None is stated publicly until rechecked from the vendor's own pages: Elastic's licence for its Observability
assistant (D82: a paid self-managed subscription, Enterprise named in a docs change, unconfirmed on the
subscriptions page; Elastic now calls it deprecated in favour of an AI Agent), Datadog Bits Chat and Sentry Seer
Agent in Slack, and SigNoz's MCP server. Recheck date: _____.

## Approval

Founder: _____ Date: _____
