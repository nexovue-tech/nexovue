# ADR-024: A case is a finding's columns, with one active case per service

## Status

Accepted 2026-11-02, with the F22 design
([`design.md`](../features/case-management/design.md), revision 2). Filed at
implementation start, as the design's approval recorded; it settles what ADR-022
left to Stage 3.

## Context

A finding closes itself when the signal recovers (ADR-022, migration 0041), so
its `state` cannot also mean "someone is still working on this". The work needs
a record: who took it, what was tried, and that recovery did not end it. A
flapping service opens several findings an hour, and a problem that returns
must land on the work already done rather than start a stranger. Three
processes will end or reopen a case — the API on a person's act, the tracker
sync, and the sweep — and the scheduler cannot import the API.

## Decision

1. **`case_state` and its columns live on `findings`** (migration 0051). There
   is no `cases` table, and there must never be one: a case is a finding with a
   non-null `case_state`.
2. **Two partial unique indexes allow one active case per service and one per
   project** (`uq_findings_project_service_active_case`,
   `uq_findings_project_active_case`), mirroring F6's open indexes because
   Postgres treats nulls as distinct. The database, not application code, is
   the arbiter: a second start joins the first.
3. **`root_finding_id` joins later findings to the case**, within 7 days of a
   person's act. A finding holds a case or joins one, never both, held by a
   check constraint.
4. **A threshold or check incident does not become a case**; it stays linked
   through `finding_incidents` (FD13, D6).
5. **An idle case ends after 90 days without a person's act, and an untouched
   one 72 hours after recovery** (both guesses, D9, D71), so stored names
   cannot outlive their retention. Traffic can never hold a case open (D42).
6. **The transitions are SQL constants in `nexovue_platform.cases`**, each with
   a typed parameter function, executed by the API and the scheduler alike
   (D77), as `RESOLVE_FINDING_SQL` already is. The timeline writer
   (`finding_event_parameters`) accepts only named, typed fields, so an event
   can never carry free text (BR9).
7. **A person's locks on a finding are `FOR NO KEY UPDATE`** (D78): no person
   act changes a key column, and a plain `FOR UPDATE` would block the foreign
   key check a new joined finding's insert takes on its root, which can
   deadlock against a detection batch.

## Alternatives rejected

- **A `cases` table** — two records for one problem, which ADR-022 exists to
  prevent; every screen and token would join them for ever.
- **Growing `incidents` too** — repeats the merge ADR-022 rejected.
- **Reusing `state`** — findings close themselves, so one column cannot carry
  both questions; the page must be able to say "recovered, still being
  investigated".
- **An automatic case per finding** — a flapping service opens many an hour
  (FD12); a case is a person's act.
- **Case logic written in the API and copied into the scheduler** — it would
  drift; the shared statements are the one spelling (AC103 proves the callers
  write identical rows).

## Consequences

A finding's meaning stays what F6 defined; its open indexes and the outbox are
untouched. The feed and page state both questions in one sentence. A check
failure with no finding has no notes or tracker link, revisited if users ask
(step I in the F22 plan). `retention.py`'s rule gains clauses in F22's Step 2.
The lock change is reasoning until the barrier test runs (T2.1), which is a
named risk in the plan.
