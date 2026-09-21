# Change: decisions-2026-09-21 — the eight open decisions, closed

## Classification

Operations — clearing a decision backlog, plus the three code changes two of them imply.

## Current behavior and problem

The whole-product console review (`docs/quality/console-review-2026-09-20.md`) separated defects
from decisions and fixed the defects. Eight decisions were left recorded and unmade, in three
different status files. A decision nobody makes is not neutral: it is re-discovered, re-argued and
re-explained every time somebody reads the code around it, and two of these had already accumulated
a **wrong** justification in writing.

## Intended behavior

Each is decided, recorded in `docs/product/vision.md` §6, and the ones that imply code are built.

### Made by the founder

1. **2FA for the super-admin role stays optional.** The step-up already demands a password *or* a
   code, and the operator is one person. Mandatory enrolment is a defensible future tightening, not
   a gap.
2. **Signup's terms link waits for the website redesign.** There is no terms page to link to, and a
   link to nothing is worse than no link.

### Decided on recommendation

3. **Paid-tier volume overage: we block.** This was recorded as open, and it was not. The four
   pricing promises already say *"over the allowance we block and say so rather than bill"*, and
   both halves of the code already do it — `allowance_service_count` in Python and
   `allowanceServiceCount` in Go size a paying tier's allowance on the customer's committed count.
   **The question is closed rather than answered.** What was genuinely wrong was a stale docstring
   in `usage/billing.py` claiming "the receiver records no allowance at all and enforces nothing" on
   a paying tier — true of a hole that was filled, and read since as a description of the design.
4. **React Flow's attribution is hidden.** `@xyflow/react` 12.11.5 is **plain MIT**: its only
   condition is that the copyright notice travel with copies of the software, which the lockfile and
   the dependency audit satisfy. There is no display requirement. The review recorded that removing
   it "would breach their terms", which was **wrong**, and that error was the reason it stayed.
   xyflow ask as a courtesy; a Pro subscription is how to support them, not a vendor badge in a paid
   console where it reads as ours. One word puts it back.
5. **The audit log opens on "Changes only".** Seventeen consecutive "signed in" rows buried the
   events the log exists for. The filter **subtracts** sign-ins rather than listing what it keeps,
   so an action kind nobody classified can never be silently hidden from a record customers read,
   and "Everything" is the next option down. Folding repeated rows was rejected: an audit log must
   not collapse its own entries, and that would have been a behaviour change needing a spec.
6. **No pagination past fifty customers.** The footer says the list is capped and search reaches the
   rest. Revisit at fifty customers, which is a good problem.
7. **Settings' General/Account split: merge, and give the organization its own screen.** Both halves
   are *you* — name and email on one, password and 2FA on the other — and an organization's name and
   region are editable nowhere. **Scheduled as its own change**, because it moves routes that
   `route-manifest.test.ts` and `unsaved-changes-coverage.test.ts` hard-code and it is an
   information-architecture change, not a rename.
8. **Publish the browser SDK to npm.** D6 decided "a script tag first and npm after", and said both
   "wait on there being somewhere to serve it from — which is what makes Phase 1's first item a
   deployment rather than a feature." That ordering had it backwards: **npm needs no hosting of
   ours**, and a public npm package is served as a script tag by jsDelivr and unpkg for free. RUM
   does not have to wait on a deployment. **Scheduled as its own change** — the package is
   `"private": true`, needs its permissive licence file (ADR-004 requires MIT or Apache-2.0, never
   AGPL, because it runs inside the customer's page), a published build and the setup docs rewritten
   from "write to us and we will send you the built file".

## Affected specifications and architecture

`docs/product/vision.md` §5 (the overage question, already amended by
`docs/changes/commitment-is-the-bill-ceiling/`) and §6 (the decision row). ADR-004 governs the
browser SDK's licence and is unchanged — decision 8 follows it rather than altering it.

## Risks and rollback

- **Hiding the attribution is a courtesy declined, not a licence breached.** The judgement rests on
  the MIT text in `node_modules/@xyflow/react/LICENSE`, read rather than recalled. If xyflow ever
  ship under different terms, this is one word in `ServiceMapScreen.tsx`.
- **A default filter is still a filter.** The audit log now opens showing a subset. It hides only
  sign-ins, it says which filter is applied, and "Everything" is one click away — but a reader who
  does not notice the control could believe they are seeing everything. That is why the filter
  subtracts rather than lists: the failure mode of the other design is unbounded.
- Decisions 7 and 8 are recorded, not built. They are named here so they are scheduled rather than
  forgotten.

## Plan and validation

1. `servicemap/ServiceMapScreen.tsx` — `hideAttribution: true`, with the licence reasoning at the
   line.
2. `audit/schemas.py`, `audit/queries.py`, `features/audit/{api.ts,audit-words.ts,AuditScreen.tsx}`
   — the `changes` kind and the default. Proved by
   `test_audit_read_live.py::test_changes_hides_sign_ins_and_nothing_else`, seen red.
3. `usage/billing.py` — the stale docstring corrected.
4. `docs/product/vision.md` §6 — the record.

## Status

See `status.md`.
