# Status: design-system-restore

## Lifecycle status

Verified

## Current task

None.

## Completed

- `docs/design/design-system.md` restored from `e0f11a0^` to the exact path four code references
  cite. All four now resolve.
- Five drifted claims corrected against measurement, each marked `[2026-09-12]` in the document:
  the shared-component line count, the "entire shared set" figure, the `useVirtualizer` caller count
  (five → nine), the brand colour's sRGB comment, and two companion documents deleted in the same
  sweep.
- `docs/design/principles.md` added: ten rules for deciding a screen nobody has designed yet,
  including the six states every screen owes and the rule that a new screen's design is settled
  before React rather than inside it.
- Wired into `README.md`, `docs/architecture/frontend.md`, `docs/engineering/frontend.md`, and
  `docs/changes/README.md`.

## Next action

None for this change.

Raised by it, not fixed here — each is the founder's call:

- **`VirtualTable` is overdue.** The restored document set five `useVirtualizer` callers as the
  threshold for a shared wrapper. There are nine.
- **The deleted ADRs.** The same sweep removed 113 decision records totalling 10,206 lines, replaced
  by three of 17 lines each. Reported separately; no change made.

## Blocked

Nothing.

## Validation

`just check` on 2026-09-12, exit code 0, output read rather than inferred: 2277 Python tests in 131s,
120 browser-SDK across 12 files, 613 web across 47 files, plus `buf lint` and `buf breaking`.

The decisive line is `packages/ui`'s own verifier, which now names a file that exists:

```
all targets met; 0 value(s) drifted from docs/design/design-system.md
tokens: no default or raw colours outside tokens.css
```

That is direct evidence the restored 28-pair APCA/WCAG table matches the shipped token values, not
merely that the build passed. It is re-measured on every `just check`, so this table cannot drift
silently again while the file exists.

Not re-run, and labelled as point-in-time records inside the document: the Chrome 148 platform
feature detection and the colourblind lightness separations, both measured in August 2026.

Every markdown link in the added and changed documents resolves; checked by script.

## Last updated

2026-09-12
