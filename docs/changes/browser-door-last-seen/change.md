# Change: browser-door-last-seen — the browser door stops marking services as heard

## Classification

Security, on the data plane (receiver → Kafka → workers → Postgres). The browser-door record found it
(`docs/changes/browser-door/status.md`, *Open decisions*). The founder decided on 2026-11-08 that it is fixed
as its own small change (same file, *Founder answer, 2026-11-08*).

## Current behavior and problem

**In plain words.** A website's public site key is printed in its page, so anyone who reads the page has it.
That key opens the browser door. Whatever service name arrives through that door, the usage meter writes it
into Postgres's `service_last_seen` table as "this service was heard just now". Every program that reads the
table treats a row as a server that is up. So anyone with a page's site key can:

- **add names** that the *service went silent* alert then watches. Ten minutes after the attacker stops,
  each made-up name opens a **critical** incident under any "every service" silence rule.
- **keep a stopped server looking alive.** Sending `service.name=checkout` from a page every few minutes
  refreshes `checkout`'s row, so a real outage of `checkout` never opens its silence incident.

It writes names and times, never counts. The rollups were already closed to the browser door
(`docs/changes/browser-door`).

**The write, read from the code** (before this change, branch `feature/phase-2` at `34808f5`):

1. The receiver stamps everything from the browser door `DOOR_RUM` on the Kafka envelope
   (`apps/receiver/internal/ingest/ingest.go:46-55,288`).
2. The usage meter decodes each envelope in `Signal.meter` (`apps/workers/internal/pipeline/usage/usage.go:281-291`
   before, now `:278-293`). It looked at the door for exactly one thing, `browserBytesOf`, which
   charges the bytes to the browser's share of the allowance. `Metered.Services`, the distinct `service.name` of
   every resource that carried something, was filled whatever the door (`countSpans`/`countLogs`/
   `countMetricPoints`, `attribute` and `serviceNameOf`).
3. `counting.absorb` feeds the result to `Meter.Observe` (`counting.go:152-161`). The meter unions the services
   per tenant-day (`meter.go`), and `counting.flush` hands the rows to `Store.Apply` (`counting.go:180`).
4. `PostgresStore.Apply` (`postgres.go:217-270`) writes, in one transaction, the offsets, `usage_daily`, the
   billing set `usage_daily_services` (`addServices`, `:146`), and **`service_last_seen`** (`markServicesSeen`,
   `:166`, via `markServicesSeen(ctx, tx, rows, newestReceived(progress))`, `:259`). That last step took
   `servicesOf(rows)`, every billed service, with the flush's newest receive time, `GREATEST` on conflict.

At that point the door is still in hand (`envelope.GetDoor()` in `Signal.meter`). The meter folds it into
`BrowserBytes` and then drops it, so nothing after `meter` could tell a browser-door name from a server's.

**Who reads `service_last_seen`** (every reader in the repository, by `grep`):

| Reader | What it does with a row | What it judges the service by | What a browser-door row does there |
|---|---|---|---|
| *Service went silent* alert, `apps/scheduler/src/nexovue_scheduler/alerts/silence.py:53-57,121-138` | Every name with a row in the last 24 h that the rule covers is judged silent or not | `last_seen_at` alone | A forged name becomes a critical incident 10 min after it stops. A forged refresh hides a real server's silence |
| Detection's active projects, `apps/scheduler/src/nexovue_scheduler/detection/projects.py:26-45` | A project with a recent row is judged by detection, and its run is "expected" (stop clock, D34) | `service_stats_1m` (`detection/store.py:172,181`) | It makes a project active that has nothing in the rollups to judge, so the clock that warns "detection stopped" starts with nothing behind it |
| SLO list, `apps/api/src/nexovue_api/slos/queries.py:124-128`, `service.py:93` | `has_services` chooses the empty state "Nothing is reporting yet" (`apps/web/src/features/slos/SlosScreen.tsx:207`) | n/a | It hides that empty state for a project whose only sender is a page |
| SLO target write, `slos/queries.py:112-121`, `service.py:126,183,298-305` | `has_reported` refuses a target naming a service the project never reported, because such a target "would read nothing, silently" | `service_stats_1m` (`apps/scheduler/src/nexovue_scheduler/slos/store.py:41`) | It lets a target name a browser-only service, which then reads nothing: exactly what the check exists to refuse |
| Deletion and tenancy (`organizations/tables.py:109`, `ServiceLastSeen` in `models.py`) | Cascade and purge only | n/a | None |
| Planned, not built: F20 dashboards (`dashboards/design.md:244,486`), F28 coverage strip and sync (`deployment-release-tracking/design.md:73,357`) | `not_in_project`/`empty` panel states, the coverage `none` list, and a skip check | Server services | Would inherit the same forged names |

**What does not read it.** These were checked because a frontend customer might depend on them:

- The services list and the alert rule's service picker call `GET /services` (`apps/web/src/features/alerts/api.ts:514`),
  which reads `endpoint_stats_1m` (`apps/api/src/nexovue_api/services/queries.py`). The browser door already
  stays out of that table (`traceassembly/stages.go:242-252`). So no screen has ever offered a browser app by name.
- RUM screens read `web_vitals_1m` (`apps/api/src/nexovue_api/rum/queries.py:54`).
- Onboarding's "first data arrived" asks `GET /traces?limit=1` (`apps/web/src/features/onboarding/api.ts:40`).
- `project_first_traces` is a separate write, the sampler's first-traces count (`apps/workers/internal/projectconfig/postgres.go:40-48`).
- nexovue's own browser SDK sends one `INTERNAL` page-view span and web-vital/error logs under a `service.name`
  that defaults to the site's host (`packages/browser-sdk/src/config.ts:110,146`; F19 spec D63). Nothing in the
  SDK or the console expects that name in `service_last_seen`.

**Root cause.** `Signal.meter` drops the envelope's door after computing `BrowserBytes`. So `markServicesSeen`
(`postgres.go`) is given every billed service, including names only an untrusted door vouched for. The table's
readers all judge servers, and the browser door was already kept out of the rollups those readers check against.

## Intended behavior

- A service name that arrives **only** through the browser door is still billed (`usage_daily_services`), and its
  bytes, spans, logs and points are still counted in `usage_daily` (with `browser_bytes`). Metering is unchanged.
- It neither **creates** nor **refreshes** a `service_last_seen` row.
- Every other door (`DOOR_OTLP`, `DOOR_SYNTHETIC`, `DOOR_POLLER`, and an envelope that names none) creates and
  refreshes rows exactly as before. A service heard through any of them in a flush is heard, even if the browser
  door also carried it in that flush.

**The fix.** `Metered.Heard` (`usage.go`) is `Services` unless the door is `DOOR_RUM` (`heardThrough`, the same
door test as `browserBytesOf` and `traceassembly.countsInRollups`). The meter unions it per tenant-day beside
`Services` (`meter.go`, `bucket.heard`, `Row.Heard`). `markServicesSeen` takes `lastSeenOf(rows)` (`postgres.go`),
the transposition of `Row.Heard`, in place of `servicesOf(rows)`. No statement, schema or reader changes.

**Why it is safe.** No reader loses anything it can use. Each reader judges a server by the rollups or by the
silence of a server, and the browser door was already out of the rollups. A browser app was never offered by
name in the rule editor or the SLO form. One behavior does change, on purpose: a silence rule scoped to
"every service" no longer watches a browser app's name. That watch was never designed or offered, and it could
not be told apart from the forgery above (see *Risks*).

**AGENTS.md invariant** (ingest writes Postgres only through a short named list): not widened. This narrows what
one of those writes, the usage meter's, accepts.

## Affected specifications and architecture

- `docs/architecture/system.md` (*Alerts*), `docs/architecture/database.md` and
  `docs/architecture/security-and-tenancy.md` each gained the clause that the browser door does not mark a service as heard.
- Specifications: none changed. F21's FR5 ("a service, or all services") is a server's silence. Its evidence
  (`alerts/design.md`) is the rollups and the meter, and nothing in it names a browser app.

## Risks and rollback

- **Rollback:** revert the commit. No migration, no data change. Rows already written by the browser door before
  the change (none exist: pre-deployment) would age out of the 24 h window by themselves.
- **"Every service" silence rules stop watching browser apps.** Deliberate, as above. If the founder wants
  "my site stopped sending" as an alert, it needs its own signal from a door the customer controls, not this table.
- **Billing still counts browser-door names** (`usage_daily_services`), so a site-key holder can still add
  billable service names. The brief kept that out of scope ("usage metering stays exactly as it is"). Recorded in
  `status.md` *Risks* for the founder.

## Plan and validation

Small enough for no `plan.md`. Steps, each done:

1. Read the write path and every reader (above).
2. Tests first, seen failing against the old write: the meter boundary (`usage_test.go`), the pipeline through a
   store model that uses the real transposition (`replay_test.go`) and the meter union (`meter_test.go`). The live
   store probe at the table (`storeprobe_test.go`, `storeprobe` tag) is written but not yet run: no Postgres
   was reachable here.
3. The fix (`usage.go`, `meter.go`, `postgres.go`).
4. `go vet -tags storeprobe ./...`, `go test -count=1 ./...`, `golangci-lint run ./...` in `apps/workers`.

Results are in `status.md` *Validation*.

## Status

See [`status.md`](status.md).
