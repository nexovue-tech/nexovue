# Status: counter-overcount-record

## Lifecycle status

Verified

## Current task

None.

## Completed

- Both overclaims in `docs/architecture/current-system-reference.md` corrected: the watermarks bound
  double-counting rather than eliminating it (line 78), and replay duplicates are *normally* absorbed
  (line 84).
- §18 carries the finding in full — +512 spans on 282,490 acknowledged, nothing lost, reproduced at
  identical magnitude across three chaos runs, the falsified premise, the untested hypothesis, and
  the reason it is accepted.
- ADRs 0060, 0061 and 0075 restored to `docs/decisions/`, so the whole chain is in the repository.
  Two links to records still only in git history were flattened to plain text naming them.
- `docs/decisions/README.md` and `decision-log.md` updated: the chain is restored and the
  contradiction is resolved rather than merely reported.

## Next action

None. The overcount is accepted and recorded.

Revisit only if it grows beyond one `loadgen` batch per restart, or appears without a restart. §18 is
where a future reader will find it, which is the point of this change.

## Blocked

Nothing.

## Validation

`just check` on 2026-09-12, exit code 0, output read: 2277 Python tests, 120 browser-SDK across 12
files, 613 web across 47 files, plus `buf lint` and `buf breaking`. No code was touched.

The code citation added to §18 was verified before being relied on:
`apps/workers/internal/pipeline/ingeststate/state.go` line 221 reads "an undercount is a worse answer
than a bounded overcount for a monitoring product", which is what the entry quotes it as saying.

All internal links among the restored decision records resolve, and the two `§18` anchors added to
lines 78 and 84 match that section's generated heading id.

**Not re-run:** the chaos measurements themselves, made 2026-08-27 and 2026-08-29. They are reported
with their dates. The untested hypothesis about non-byte-identical re-derived blocks is labelled
untested in both the ADR and the §18 entry.

## Last updated

2026-09-12
