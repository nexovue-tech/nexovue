# Status: platform-list-first

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- **The API lists without a needle.** `queries.search_organizations(session, needle=None)` drops the
  `or_()` clause entirely rather than matching no row; `service.search(db, needle=None)` returns
  `truncated=len(found) >= SEARCH_LIMIT`; `GET /admin/organizations?q=` is optional. A one-character
  needle is still refused with "Search for at least two characters."
- **nexovue's own organization is excluded on both paths**, not only the search one. It has no
  project, no usage and nothing to suspend, and it matches the word an operator is most likely to
  type.
- **The screen is a table.** Organization, Region, Plan, Created and State in shared column widths,
  with a footer that reads "The newest 50. Search to reach the rest." when the cap cut the list and
  "N customers." when it did not.
- **The search box narrows what is already there** rather than producing it: `enabled: needle.length
  !== 1`, so the list loads on open and reloads on a real needle.

## Next action

Founder: review the diff and say **push**.

## Open decisions

- **No pagination past fifty.** The footer says the list is capped and the operator searches. A page
  is a change of its own, when there is a customer count that needs one.

## Blocked

Nothing.

## Risks

- **The step-up now fires when the screen opens**, because the list is the first read. That is the
  intended reading — the confirmation belongs to entering the platform area, not to the third
  keystroke of a search — but it is a visible change in when the password is asked for.
- **Fifty rows is more on screen than a search box was.** Same role gate, same 30-minute step-up, and
  no member address or telemetry on the list.

## Validation

- `test_admin_live.py` — the list comes back without `q`; `truncated` is true at a monkeypatched
  `SEARCH_LIMIT` of 1 and false below it; the platform organization is absent with and without a
  needle.
- `PlatformScreens.test.ts` — rows render with no needle, and the footer names the cap.
- `just check` on 2026-09-21: **4118 Python passed, 2 skipped**; **1333 web** (122 files); **120**
  browser SDK; Go, format, lint, `mypy` (572 files) and the contract checks clean.
  - One failure, environmental and not this change's:
    `test_alert_telemetry_live.py::test_error_groups_come_back_with_the_errors_screens_identity`
    needs a span carrying an exception *event* in the last 24 hours, which only `just otel-demo up`
    produces here. Proved on a clean worktree of `HEAD`, where it fails identically.
- **Looked at, not just tested** — walked in a browser with the founder watching, which is where the
  two step-up defects recorded in `console-review-fixes` were found.

## Specification impact

`docs/features/team-and-api-access/spec.md` — **FR25 amended**: the platform screen lists every
customer and the search narrows that list, rather than the search being the only way to see one.
ADR-018 (the admin search is cross-tenant by design) is unchanged.

## Documentation impact

None beyond this record. No architecture document describes the screen's opening state.

## Last updated

2026-09-21
