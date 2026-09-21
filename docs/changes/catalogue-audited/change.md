# Change: catalogue-audited — the roadmap checked against the code, not re-read

## Classification

Operations — documentation correctness, plus one test defect the audit's own gate exposed.

## Current behavior and problem

Three documents were found stale in one day, each describing work that had already shipped:

- `docs/changes/alert-links-to-incident/status.md` said every API write commits after its response
  is sent. `dcfc9b2` fixed that, and the entry sent this session chasing it as the top pending item.
- `docs/operations/production-readiness.md` blocker 3 said signup was public, that there was no
  invite flow and that no setting closed it. F26 and F31 had shipped all three.
- `docs/features/{alerts,profiling}/status.md` and eight change records said work was uncommitted
  that was in history.

So the catalogue itself could not be trusted, and it was read rather than checked.

**It contradicted itself.** F31 and F36 both carried `🔴 No` in the *Built* column of a row whose
own note read "Built 2026-09-20".

## Intended behavior

Every claim in the roadmap checked against the code that would prove or disprove it.

**Corrected:**

- **F31 and F36 → `✅ Yes`.** Verified by the modules that implement them, not by the note beside
  them.
- **Track 1 is complete** — all six features built, committed, dated. So Track 3 is the critical
  path now rather than a parallel one, and the document says so.
- **F37 is the only Phase 1 feature still unbuilt and unspecified**, and it owns readiness blockers
  9 and 10 — the same work counted twice across two bullets. Its row now records the two pieces that
  landed on 2026-09-21 and that both are unrehearsed.
- **Ten of eleven blockers remain.** Blocker 3 cleared; the other ten re-checked against the code.
- **F25 → `🔶 PART SPECIFIED`**, with the specification written 2026-09-21 awaiting approval.
- **The eleven unspecified built features are named** where somebody reading the catalogue will see
  them, with the reason it is not a formality.

**Confirmed as already accurate**, so they are not changed: F1's missing engine, F20's missing
builder, F24's absent AWS role flow, and the partial states of F27 and F28.

## Affected specifications and architecture

None. `docs/product/feature-catalog.md` is a roadmap, not a contract.

## The test defect the audit found

Running the gate for this change failed
`test_tenant_isolation_live.py::test_a_new_organizations_trace_list_holds_nothing_but_its_own` with
an empty set, then passed when run alone.

Not flakiness to be re-run away. `_wait_until_readable` waited for `GET /traces/{id}` — the **span
rows** — and the test then asserted on the trace **list**, which is served from the summary the
assembler derives afterwards. It waited for one write and asserted on another.

Serial, the summary landed inside the first three-second poll and the gap never showed. Six workers
(`docs/changes/faster-gate/`) made the stack busy enough to expose it. The helper now waits for
both, and says why.

This is the third time in two days that waiting for the wrong thing has produced a confusing red —
`gateseed`'s baseline was the second.

## Risks and rollback

- **A roadmap audited today drifts tomorrow.** Nothing here stops that; what it does is record how
  each claim was checked, so the next audit can repeat it rather than re-derive it.
- The wait is now two requests per poll instead of one, against a stack the suite is already
  waiting on. It shortens no deadline.

## Plan and validation

1. `docs/product/feature-catalog.md` — the header, F31, F36, F25, F37, Track 1, Track 3's blocker
   bullet, and the unspecified-features note.
2. `apps/api/tests/test_tenant_isolation_live.py` — `_wait_until_readable` waits for the list too.

## Status

See `status.md`.
