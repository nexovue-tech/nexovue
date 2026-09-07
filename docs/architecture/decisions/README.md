# Architecture Decision Records

One decision per file. The chronological index of every decision lives in
[`../system-architecture.md` §6](../system-architecture.md#6-decisions-log); this directory holds
the full record of each one — what the situation was, what we chose, and what it costs us.

## Format

Copy [`0000-template.md`](0000-template.md). Four things: **Status**, **Date**, **Context**,
**Decision**, **Consequences**. Keep it to one screen. A template nobody wants to fill in does not
get filled in, and an ADR nobody wants to read does not get read.

The **Consequences** section is the one that earns its keep. Record the cost accepted, not only the
benefit — a year later that is the part no one can reconstruct.

## Numbering

`NNNN-kebab-slug.md`, zero-padded to four digits, allocated in order. Numbers are never reused and
never renumbered. `0000` is the template.

## ADRs are immutable

**Once an ADR is accepted it is never edited.** Not to fix the reasoning, not because we changed our
minds, not because the world moved. A decision record is a record of what we believed at a point in
time; rewriting it destroys the only thing it was for.

When a decision changes, write a **new** ADR and link the two:

- the new ADR gets `**Supersedes:** NNNN-slug.md` (as a link)
- the old ADR's status becomes `Superseded by MMMM-slug.md` (as a link)

Updating the old file's *status line and link* is the single permitted edit to a decision —
everything below it stays as written.

**One further edit is permitted, and only this one: correcting a fact in the evidence.** An ADR
carries two different kinds of writing — the decision and the reasoning, which are a record of what
we believed and must never move, and the *measurements* offered in support, which are claims about
the world and can simply be wrong. A wrong measurement left standing teaches the next reader
something false; a superseding ADR is the wrong instrument for it, because it announces that the
decision moved when the decision did not, and a supersession chain full of factual typos stops
being useful for finding the reversals it exists to record.

So: add an `**Evidence corrected:**` line immediately under the status block, dated, quoting the
wrong claim and stating the true one with the command that established it. **The body stays
untouched**, including the wrong sentence — a reader must still be able to see what we believed
when we decided. If the correction changes the *decision*, it is not a correction; write a new ADR. A superseded ADR must still stand on its own: a reader should finish it
understanding why the decision looked right at the time.

Where a later ADR changes only part of an earlier one, say so — "superseded in part" — and name the
part. The rest of the old ADR remains in force.
