# Status: browser-door-last-seen

## Lifecycle status

Implemented, 2026-11-08, on `feature/phase-2`. Live store probe run red and green; `just check` passed.

## Current task

None. Built and self-reviewed (`/review-diff`). Waiting for the coordinator's review.

## Completed

- Record opened and listed in `docs/changes/README.md`. The browser-door record's founder-answer section links here.
- Current system read from the code: the write path and every reader of `service_last_seen` (`change.md`,
  *Current behavior and problem*).
- Tests written and seen failing against the old write. Then the fix: `usage.go` (`Metered.Heard`,
  `heardThrough`), `meter.go` (`bucket.heard`, `Row.Heard`), `postgres.go` (`lastSeenOf` feeds
  `markServicesSeen`; `servicesOf` and `lastSeenOf` share `namesOf`).
- `docs/architecture/system.md`, `database.md` and `security-and-tenancy.md` each gained one clause.

## Next action

None for this change once committed. The founder decides the billing question under *Risks*.

## Open decisions

None blocking. One note for the founder, decided by the code and not left open:

- **A silence rule scoped to "every service" no longer watches a browser app's name.** The intended direction
  was given, and the code makes it clear-cut. Every reader of `service_last_seen` judges servers: detection and
  SLOs check against `service_stats_1m`, which the browser door was already kept out of. No screen offers a
  browser app by name, because the rule editor's picker and the services list read `endpoint_stats_1m`. And a
  browser-door name cannot be told apart from a forged one. If "my site stopped sending" is wanted as an alert,
  it is a feature with its own signal, not this table.

## Blocked

Nothing.

## Risks

- **Only the live store probe guards the Postgres call.** The offline unit and replay tests model the store through
  `lastSeenOf` itself, so reverting `markServicesSeen` to `servicesOf` leaves them green (seen 2026-11-08). The
  storeprobe catches it (below), but it runs only against a live Postgres, which `just check` and CI do not start,
  the same as the existing `TestServiceLastSeenOnlyMovesForward`.
- **Billing still takes browser-door names.** A site-key holder can still add names to `usage_daily_services`,
  the billing unit (priced per service). Out of this change by the brief ("usage metering stays exactly as it
  is"), and the browser-door record does not cover it either. For the founder: whether a browser-door name
  should be billable is a pricing decision. The commitment ceiling
  (`docs/changes/commitment-is-the-bill-ceiling`) may already bound the charge; not checked here.
- **The SLO screen's empty state for a project whose only sender is a page** now reads "Nothing is reporting
  yet" (`apps/web/src/features/slos/SlosScreen.tsx:207`). Its detail line is true: "A target counts a
  service's requests, so it needs one sending them first". The title is loose for a project that is sending
  page views. The copy was not changed here.
- **Doors other than the browser's are trusted as before.** `DOOR_SYNTHETIC` and `DOOR_POLLER` still mark
  their services as heard. Only the scheduler stamps them. Not reviewed further.

## Validation

**Seen failing first** (`go test -count=1 ./internal/pipeline/usage/`, from `apps/workers`). This ran with the
tests in place and the old write: the new `Row.Heard`/`Metered.Heard` fields existed but were unpopulated, and
`markServicesSeen` still took every billed service.

```
--- FAIL: TestABrowserDoorBatchNeitherCreatesNorRefreshesALastSeenRow (0.00s)
    replay_test.go:505: a browser-door batch refreshed checkout to 2026-08-30 10:40:00 +0000 UTC, want it left at 2026-08-30 10:30:00 +0000 UTC
    replay_test.go:508: a browser-door batch created a last-seen row for minted at 2026-08-30 10:40:00 +0000 UTC
--- FAIL: TestOnlyTheBrowserDoorMarksNoServiceHeard (0.00s)
    --- FAIL: TestOnlyTheBrowserDoorMarksNoServiceHeard/usage-counting-spans/DOOR_OTLP (0.00s)
        usage_test.go:387: heard = [], want [checkout]
    ... (the same for DOOR_SYNTHETIC, DOOR_POLLER, DOOR_UNSPECIFIED, on spans, logs and metrics: 12 subtests)
FAIL	github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/usage
```

The control direction was seen red too. With the fix in place but `Meter.Observe` not collecting `Heard`:

```
--- FAIL: TestHeardIsTheUnionOfTheServicesAnyOtherDoorCarried (0.00s)
    meter_test.go:257: heard = [], want checkout alone
--- FAIL: TestABrowserDoorBatchNeitherCreatesNorRefreshesALastSeenRow (0.00s)
    replay_test.go:500: after the server door: checkout last seen 0001-01-01 00:00:00 +0000 UTC (row false), want 2026-08-30 10:30:00 +0000 UTC
```

So a fix that dropped every name would also fail: the server door must still create and refresh.

**Passing after the fix**, in `apps/workers`:

- `go vet -tags storeprobe ./...`: clean.
- `go test -count=1 ./...`: 28 packages ok, 528 top-level tests passed, 0 failed. Before this change the
  browser-door record counted 525; this change adds 3.
- `golangci-lint run ./...`: 0 issues.
- The three new tests pass: `TestOnlyTheBrowserDoorMarksNoServiceHeard` (15 subtests: 5 doors × spans, logs,
  metrics; usage bytes and billed services unchanged for each), `TestABrowserDoorBatchNeitherCreatesNorRefreshesALastSeenRow`
  (server door creates, browser door neither refreshes `checkout` nor creates `minted` while spans, billed
  services and browser bytes still count, server door refreshes again), `TestHeardIsTheUnionOfTheServicesAnyOtherDoorCarried`.

**Live store probe, run by the coordinator, 2026-11-08,** against Postgres 18 (pgvector image) migrated to the
branch head (Alembic 0052), from `apps/workers` with `go test -tags storeprobe -count=1 ./internal/pipeline/usage/`:

- **First run:** the new probe passed, and the existing `TestServiceLastSeenOnlyMovesForward` failed with
  `reading the row back: no rows in result set`. Cause: that probe builds its `Row` by hand with `Services` only,
  and last-seen is now written from `Heard`, which the meter always fills in production. Fixed in the test fixture
  (`Heard: []string{"checkout"}`), not in production code.
- **Red, with `markServicesSeen` fed `servicesOf(rows)` again as before this change:**
  ```
  storeprobe_test.go:197: checkout last seen 2026-11-08 10:10:00 +0000 UTC, want 2026-11-08 10:00:00 +0000 UTC: the browser-door flush refreshed it
  storeprobe_test.go:200: minted has a last-seen row at 2026-11-08 10:10:00 +0000 UTC; the browser door created it
  --- FAIL: TestABrowserDoorFlushLeavesServiceLastSeenAlone
  ```
- **Green, with the change:** all seven tests in the package pass under the tag, both probes included (`ok`).

The probe database was removed afterwards.

**Not run:** Python checks (no Python changed);
the other Go modules (`apps/receiver`, `packages/contracts`, `tools/*`), which this change does not touch.

## Specification impact

None. F21 FR5's *service went silent* is a server's silence. Nothing in the alerts spec or design names a
browser app as a silence subject.

## Documentation impact

- Updated: `docs/architecture/system.md` (*Alerts*), `docs/architecture/database.md`,
  `docs/architecture/security-and-tenancy.md`, `docs/changes/README.md`, and one link line in
  `docs/changes/browser-door/status.md`.
- **Now stale, not edited here (another record's text):** `docs/changes/browser-door/status.md`, *Open
  decisions*, says "Legitimate browser services need that row". The code shows no reader that does
  (`change.md`, the readers table). The browser-door record is the place to correct it.
- F20's and F28's designs plan to read `service_last_seen` (`dashboards/design.md:244,486`,
  `deployment-release-tracking/design.md:73,357`) as a server-service list. That still holds, and now cannot be
  fed from a page.

## Last updated

2026-11-08: record opened, built and self-reviewed. State Implemented. Next action: coordinator review, the
live store probe, `just check`, commit.
