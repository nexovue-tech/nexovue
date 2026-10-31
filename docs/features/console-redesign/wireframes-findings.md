# Wireframe — The Findings feed (batch 5)

> **Status:** Awaiting approval. Resolves OD1.
>
> **This wireframe is provisional, and deliberately so.** Nothing produces findings. F1's
> specification describes a card, and drawing it here risks that drawing becoming the de facto
> requirement for an insight engine nobody has designed. The structure below is committed; the card's
> contents are a sketch the engine may overrule.

## What is committed and what is not

| Committed | Provisional |
|---|---|
| Findings is its own entry, and takes `/` from Overview when the engine exists | What a finding card contains |
| The Feed archetype: statements, newest first, Statement density | The wording and shape of a probable cause |
| The healthy state, which F1 AC1.7 already specifies | The impact estimate and its confidence rule |
| Acknowledge and resolve as row actions | How findings group and deduplicate |

The right-hand column is the engine's to settle. Recording that boundary is the point of this
document.

## The feed

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Findings                          [ Active ▾ ] [ All services ▾ ] [ 24h ]│
├──────────────────────────────────────────────────────────────────────────┤
│ ●  Checkout is 4× slower than normal since 15:04                         │
│    checkout-api · POST /checkout                                         │
│    ~12% of checkout requests affected · started 22m ago                  │
│    Probably: payment-gateway calls went from 340 ms to 3.9 s             │
│    ▁▂▃▅▇█▇                                            [ Ack ]  [ Resolve ]│
├──────────────────────────────────────────────────────────────────────────┤
│ ▪  inventory-sync has been failing 9% of the time since 14:20            │
│    inventory-sync · queue                                                │
│    218 of 2,410 messages · started 66m ago                               │
│    ▁▁▂▂▃▃▃                                            [ Ack ]  [ Resolve ]│
├──────────────────────────────────────────────────────────────────────────┤
│    RESOLVED 09:12   Catalog latency returned to normal          2h ago   │
└──────────────────────────────────────────────────────────────────────────┘
```

**The title is the product.** A finding is a sentence — subject, what changed, magnitude, since when.
F1 AC1.3 forbids raw metric names, internal ids and unexplained percentile jargon in a title, and
that constraint belongs to the engine as much as to the screen: a screen cannot rescue a title that
arrives as `http_server_duration_p99{service="checkout-api"} > 3.2`.

**Ordering is active-before-resolved, then severity, then recency** (F1 AC1.5). Resolved findings
stay visible but visibly quiet — a single line, no sparkline, no actions.

**The sparkline is the same component as everywhere else.** A finding is an entity with a rollup
behind it, so the archetype system applies rather than a bespoke chart.

## The healthy state is the one that matters

F1 AC1.7 requires it explicitly, and it is the state an insight-first product is judged on: when
nothing is wrong, silence must read as information rather than as a broken page.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  All 3 synthetic checks are passing                                      │
│                                                                          │
│  11 services reporting · 3 synthetic checks passing                      │
│  Last finding resolved 2 hours ago                                       │
│                                                                          │
│  ─────────────────────────────────────────────────────────────────────   │
│  8.4k req/min    1.2% errors    p95 210 ms          ← Overview's summary │
└──────────────────────────────────────────────────────────────────────────┘
```

The headline reports only what the checks said. "All systems normal" waits for an engine that can
assess the system (`docs/changes/findings-false-all-clear/bug.md`).

This is where Option 2 from the OD2 discussion survives: the healthy Findings screen carries the RED
summary, because a page with only a sentence gives a reader no way to confirm it. When
findings exist they lead and the summary drops away; when they do not, the summary is the content.

**Until the engine exists, this healthy state is the entire screen** — which is precisely why
Overview holds `/` and Findings does not.

## States

*Loading* — skeleton statements at Statement density, not row density. *Empty* is the healthy state
above, never a blank page or a spinner (F1 AC1.7 forbids both by name). *Error* — the feed failed to
load, which must be visibly different from "nothing is wrong"; conflating them would tell a customer
their system is healthy when the truth is that we cannot see it. That distinction is the single most
dangerous failure on this screen.

## What this wireframe deliberately does not do

It does not specify how findings are generated, grouped, deduplicated, or scored. F1's AC1.9 requires
that a continuing incident updates one card rather than duplicating — that is a grouping rule owned
by the engine, and a screen that assumed it would be encoding a requirement nobody has agreed.
