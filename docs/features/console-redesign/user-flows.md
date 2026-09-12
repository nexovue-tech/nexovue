# User flows

> **Status:** Informed guesses, not research. Nexovue has no users, so nothing here was observed —
> these are traced from the personas in `docs/product/vision.md` against screens that actually exist.
> Treat every step as a hypothesis to be corrected by the first real usability session.
>
> **Purpose:** a flow that crosses four screens to answer one question is an information-architecture
> defect. These exist to find those before any screen is drawn.

## The three personas

| | Who | What they want | Surfaces |
|---|---|---|---|
| **P1 Dev** | Full-stack dev at a 2–15 person startup, also the ops person | Root cause, fast | Most of the console |
| **P2 Indie** | Solo founder / indie hacker | Value in 10 minutes, no surprise bill | Onboarding, synthetics, usage |
| **P3 Owner** | Non-technical site owner | "Is my site okay?" | **None — F7 is not built** |

**P3 does not use the console.** Their only named surface is the Owner status page, which is
unspecified and unbuilt. Designing console screens for P3 would be inventing a user. Recorded so the
redesign does not quietly widen to serve someone who has nowhere to stand.

---

## Flow A — P1 Dev, an incident

The flow the product exists for.

```
 0  Something breaks
       │
       ▼
 1  How does the Dev find out?          ← ✗ NO TRIGGER EXISTS
       │                                   Alerts are F21, not built.
       │                                   Today: a customer tells them, or they happen to look.
       ▼
 2  Opens the console      →  /  Findings     ← ✗ PLACEHOLDER
       │                                        The front door answers nothing.
       ▼
 3  "Which service is unhealthy?"       →  /services
       │                                    Ranked worst-first. This step works.
       ▼
 4  "What is wrong with checkout-api?"  →  /services/checkout-api
       │                                    Rate, errors, p95, endpoint breakdown.
       ▼
 5  "Show me the actual failed requests" →  /traces  (filter by service)
       │                                    ⚠ Filter must be re-applied by hand.
       ▼
 6  "What happened inside one?"         →  /traces/{id}
       │                                    Waterfall. Correlated logs are here.
       ▼
 7  "That DB span is slow — which query?" →  /database
       │                                    ⚠ CONTEXT LOST. A separate screen,
       │                                      not scoped to the trace that led here.
       ▼
 8  "Did a deploy cause this?"          →  ✗ NO SCREEN
                                            deploy_events is ingested, stored, and
                                            served by the API. Nothing displays it.
```

**What this flow exposes**

1. **The journey has no beginning and no end.** Step 1 has no trigger and step 8 has no screen. The
   middle five steps work.
2. **Four hops from "something is wrong" to "here is the failing request."** Steps 3→4→5→6. Each is
   individually reasonable; together they are the product's core loop and it is long.
3. **Context does not travel.** The service chosen at step 4 is not carried into step 5. The trace
   opened at step 6 does not scope step 7. Every screen starts over.
4. **Deploy correlation is impossible today**, and it is the single most common real cause. The data
   is already there.

**IA consequences for the redesign**

- Selection must travel between screens. If a Dev is looking at `checkout-api`, every screen they
  open next should already be scoped to it.
- `/database` is reached *from a trace* far more often than it is browsed. That argues for it being a
  view that can be scoped, not only a top-level destination — which supports merging it with
  `/infrastructure` under one "Operate" destination and letting the trace link into it scoped.
- Deploy events need a home. Cheapest honest answer: a deploy marker on every time series, plus a
  list. Not a new top-level screen.

---

## Flow B — P2 Indie, first ten minutes

The flow that decides whether nexovue is ever used twice.

```
 1  Signs up                    →  /signup
 2  "Now what?"                 →  /onboarding      — two env vars, one key
 3  Waits for first data        →  polls the trace list until non-empty
 4  "Did it work?"              →  /traces          ⚠ lands on raw traces
 5  "Is my site up?"            →  /synthetics      — needs a check created first
 6  "Will this cost me?"        →  /usage
```

**What this flow exposes**

1. **Step 4 lands the newest user on the most technical screen in the product.** The first thing an
   indie founder sees after connecting is a raw trace list. The one thing they wanted to know was
   "did it work," and a non-empty list is only an implicit yes.
2. **Synthetics needs a check before it says anything** — the fastest zero-instrumentation value in
   the product, behind a setup step, on the screen a new user is least likely to find.
3. **Usage is an account screen, not a product screen.** "Will this cost me" is a first-day question
   for this persona and it is filed under settings-adjacent navigation.

**IA consequence:** the post-onboarding destination should be a confirmation, not a trace list. This
is the strongest argument in either flow for the Findings feed being genuinely first-class — it is
the only screen that could say "your data is arriving and nothing is wrong" in one sentence.

---

## Flow C — P3 Owner

There is no flow. F7 is unbuilt and unspecified, and the console has no screen this persona can use.

Recorded as a gap in the product, not a gap in this redesign.

---

## What these flows change in the design

| Finding | Consequence |
|---|---|
| Context is lost between every screen | Selection must travel; scoping is a first-class concern, not a filter the user re-applies |
| Four hops to a failing request | Each ranked-entity row should reach the next level directly, not via an intermediate screen |
| No deploy screen, data exists | Deploy markers on series, plus a list — not a new top-level destination |
| `/database` reached from traces | Supports merging it with `/infrastructure`, reachable scoped from a trace |
| Indie lands on `/traces` after onboarding | The post-onboarding destination must confirm, not dump |
| No alert trigger, no findings | Both flows start at a screen that cannot yet answer them. The redesign designs the shell; the engine remains out of scope |
