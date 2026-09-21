# Change: platform-list-first — the operator sees the customers before searching for one

## Classification

Refactor — a behaviour change to the admin console's first screen, by founder decision 2026-09-21.

## Current behavior and problem

`/admin` opened as an empty box. `GET /admin/organizations` required `q`, the console's query was
`enabled: needle.length >= 2`, and until the operator typed two characters the screen said nothing
about who the customers were.

That is search built for a directory of thousands. nexovue has one operator and a handful of
customers, and the operator opening this screen usually wants to *see who they are* — not to recall
a name they already know. The screen answered a question nobody had asked and withheld the one they
had.

It also made the step-up worse. The first read is what demands the 30-minute confirmation, so the
password was demanded on the third keystroke of a search rather than when the screen opened, which
read as a control firing at random.

## Intended behavior

The screen is a **table of every customer, newest first**, and the search box narrows it.

- `q` is optional. Without it the API returns the newest `SEARCH_LIMIT` (50) organizations.
- The response carries `truncated`, so the screen can say *"The newest 50. Search to reach the
  rest."* rather than implying it has shown everything.
- nexovue's own organization is excluded either way: it is not a customer, it has no project and no
  usage, and it matches "nexovue" — the word an operator is most likely to type.
- The table shows Organization, Region, Plan, Created and State in shared column widths, so a
  suspended or comped customer is visible without opening anyone.
- **One character is still refused** (`MIN_NEEDLE`, "Search for at least two characters."). No
  needle is not a short needle: it is the screen asking who the customers are, and a single letter
  matching most of the list reads as a broken filter. The console does not send that request at all.

## Affected specifications and architecture

`docs/features/team-and-api-access/spec.md` describes the admin console's organization search. The
search still exists; what changed is that it narrows a list rather than producing one. ADR-018 (the
admin search is cross-tenant by design) is unchanged — the exemption in
`test_postgres_tenancy_structure` already covers an unscoped `search_organizations`.

## Risks and rollback

- **A list is more data on screen than a search box.** Every row is an organization name, region and
  plan — which the operator is authorized to see, behind the same role gate and the same 30-minute
  step-up as before. No member address or telemetry is on the list.
- **Fifty is a cap, not a page.** There is no pagination; past fifty customers the footer says so and
  the operator searches. Adding a page is a change of its own when there is a customer count that
  needs one.
- Rollback is restoring the required `q` and the `>= 2` gate. Nothing migrated.

## Plan and validation

1. `admin/queries.py` — `needle` optional; the `or_()` clause dropped entirely when absent.
2. `admin/service.py` / `schemas.py` — `search(db, needle=None)`, `OrganizationSearch.truncated`.
3. `admin/router.py` — `q` optional.
4. `features/admin/api.ts` — `enabled: needle.length !== 1` (0 or 2+, never the one-character case
   that matches everything); the needle is omitted from the query string when empty.
5. `features/admin/PlatformScreen.tsx` — the table, the shared widths, the footer line.

Proved by `test_admin_live.py` (list without `q`, `truncated` at a monkeypatched limit of 1, the
platform organization absent from both paths) and `PlatformScreens.test.ts` (rows render without a
needle; the footer names the cap).

## Status

See `status.md`.
