---
name: explore
description: >-
  Thinking through an idea that is not yet a requirement — brainstorming,
  weighing approaches, or working out what something should be before any
  spec exists. Use when Raj says he is not sure yet, wants to think or talk
  something through, asks what a capability should look like, or has an idea
  without acceptance criteria. Also use when feature-work or change-work is
  already underway and hits a decision nobody has made. Not for a request
  that already states what it wants — that is feature-work or change-work.
---

# Explore

Exploration ends in a decision or a specification. It never ends in code.

Follow [`docs/engineering/workflow.md`](../../../docs/engineering/workflow.md) afterwards; this
skill is what happens *before* its first gate.

## 1. Ground before inventing

Read what exists first, and say what you found before proposing anything:

- `docs/product/feature-catalog.md` — the capability may already have an entry, a spec, or open
  questions someone already wrote.
- `docs/decisions/decision-log.md` — 112 earlier decisions. **Several questions that sound open are
  already answered**, including ones decided, reversed, and reversed back.
- `docs/architecture/` and then the code it names. Code wins over any document.

Half of "we should build X" turns out to be partly built here. Saying so early is the single most
useful thing this step does.

## 2. What not to do while exploring

- **No code.** Not a sketch, not a file, not "just to see". The moment code appears the idea stops
  being negotiable, because arguing with something that exists is harder than arguing with a
  sentence.
- **No spec, no design document, no workspace.** Those are the next step and they have gates.
- **No invented requirement.** If the answer depends on something only Raj knows — who this is for,
  what it is worth, what is acceptable — that is a question, not an assumption to fill in.
- **No single recommendation presented as the only option.** Exploration is where alternatives are
  still cheap.

## 3. Separate the two kinds of question

State which is which, explicitly, every time:

| Raj decides | You recommend |
|---|---|
| Who this is for, and whether it is worth building | Which technical approach, and the tradeoff |
| Scope, priority, what is deliberately left out | What the code makes cheap or expensive |
| Acceptable cost, risk, and failure modes | What is already built and reusable |

Blurring these is the specific failure this skill exists to prevent. An exploration that quietly
settles a product question has made the decision for him.

## 4. Push back

Raj asks for disagreement rather than agreement. Say plainly when an idea conflicts with a recorded
decision, duplicates something built, or costs more than it appears to. If he reaffirms after
hearing it, that is the decision — implement it fully and stop relitigating.

## 5. Converge

Exploration is finished when one of these is true, and you say which:

- **A decision was reached** — record it. A material technical choice becomes an ADR in
  `docs/decisions/`. A product choice goes to the catalog entry or the feature's Open decisions.
- **It is ready to specify** — hand off to `/feature-work`, which opens `spec.md` in `Proposed`
  with what is settled and what is still open.
- **It is not worth building now** — say so and record why in the catalog, so it is not re-explored
  from scratch in three months.
- **It needs something that does not exist yet** — name the missing thing. That is usually the real
  finding.

**Nothing is written until one of those is true.** A brainstorm with no artifact is lost work:
`status.md` is the source of truth, and chat history is not.

## 6. Resuming interrupted work

When `/feature-work` or `/change-work` hits a decision nobody has made: stop that work, finish
anything independent of the answer, and explore just the open question. Record the outcome in the
workspace already open — its `design.md` or Open decisions — then resume where you stopped. Do not
open a second workspace for the question.
