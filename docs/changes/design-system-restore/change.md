# Change: design-system-restore — the specification the token file names

## Classification

Bug (documentation)

## Current behavior and problem

`docs/design/design-system.md` was deleted in `e0f11a0 "delate the docmnets unwanted"`. It was 379
lines and had been maintained across four commits. **Four references to it survived in shipped code:**

| Reference | What it claims |
|---|---|
| `packages/ui/tokens.css:2` | "Specification: docs/design/design-system.md. The OKLCH values below are transcribed from that document unchanged" |
| `packages/ui/scripts/verify-contrast.js:6` | names it as the source of the APCA values being verified |
| `packages/ui/scripts/verify-contrast.js:83` | "One row of the verification table in docs/design/design-system.md" |
| `packages/ui/scripts/verify-contrast.js:227` | failure message: "value(s) drifted from docs/design/design-system.md" |

So `verify-contrast.js` — which runs inside `just check` — could fail the build with an error telling
the reader to consult a file that does not exist, and `tokens.css` declared itself a transcription of
a missing specification.

This is the failure mode `AGENTS.md` names directly: a standard that no longer matches the code is
worse than none, because it is trusted. Absent and still cited by name is worse again.

Separately, nothing recorded the design rules that generalise past colour — the six states a screen
owes, when a control may be custom, or that a screen's design is settled before React rather than
inside it.

## Intended behavior

- `docs/design/design-system.md` restored to the exact path the code cites, so all four references
  resolve.
- Every factual claim in it re-verified against the code. Where the code had moved, **the document is
  corrected, not the code** — current state is the code's to define.
- A new `docs/design/principles.md` holds the ten rules that decide a screen nobody has designed yet.
  It does not restate the visual system; it governs what belongs on a screen at all.

## Drift found and corrected on restore

| Claim in the deleted document | Measured 2026-09-12 |
|---|---|
| Shared components "535 lines in total" | **531** |
| "599 lines for the entire shared set" | 531 components + 143 toast + 295 app shell; the old figure did not say what it counted |
| `useVirtualizer` called in "the five screens that need it" | **nine** — infrastructure, errors, services and service detail were added since |
| Brand colour heading `#0BEA91` | `packages/ui/tokens.css` reads `#0AEA91` in every block; the OKLCH value is the definition and the token file was right |
| Companion docs `../architecture/repository-structure.md`, `../architecture/technology-choices.md` | both deleted in the same sweep; re-pointed at the current architecture and engineering documents |

The `useVirtualizer` drift is the instructive one. The document itself set five callers as the point
at which a shared `VirtualTable` would earn its place. That threshold passed unnoticed while the
document was deleted, and the count reached nine. `VirtualTable` is now overdue rather than
debatable — a concrete cost of having removed the standard.

## Affected specifications and architecture

No product behavior, API, schema, or service changes. `docs/architecture/frontend.md` and
`docs/engineering/frontend.md` now name where the tokens are specified; the root `README.md` lists
the design documents.

## Risks and rollback

The restored document asserts measurements — the APCA and WCAG table, the Chrome 148 feature
detection, the colourblind lightness separations — that were made in August 2026 and were not re-run
here. The contrast table is the one continuously re-verified, because `verify-contrast.js` measures
it on every `just check`. The feature-detection table is a point-in-time record and is labelled as
one.

Rollback is deleting two documents. Nothing depends on them at runtime, though deleting them
re-creates the defect this change fixes.

## Plan and validation

1. Restore from `e0f11a0^` rather than rewriting, so the original evidence and reasoning survive.
2. Verify every factual claim against the code; correct the document where it drifted, marking each.
3. Add `principles.md`.
4. Wire both into the README, architecture, and engineering indexes.
5. Run `just check` — `verify-contrast.js` and `verify-tokens.js` run inside it via `just test-web`.

## Status

See [status.md](status.md).
