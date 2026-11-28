# Change: browser-door — the browser door stops counting in the rollups

## Classification

Security — on the data plane (receiver → Kafka → workers → ClickHouse).

## Current behavior and problem

A website's public site key is printed in its page, so anyone who can read the page holds it. That
key opens the browser door (`/browser/v1/traces`, `/browser/v1/logs`, `/browser/v1/metrics` —
`apps/receiver/internal/otlp/handler.go:198`, `browserdoor_test.go:32-34`), and the receiver stamps
everything that comes through it `DOOR_RUM` on the Kafka envelope
(`apps/receiver/internal/ingest/ingest.go:46-55,288`; `packages/contracts/proto/nexovue/v1/envelope.proto:9-15,40`).

From there, nothing looks at the stamp except the usage meter
(`apps/workers/internal/pipeline/usage/usage.go:281-291`). The worker takes `service.name` from the
sender's own resource attributes (`apps/workers/internal/pipeline/traceassembly/flatten.go:142`) and
counts every `SERVER` or `CONSUMER` span into `endpoint_stats_1m` and `service_stats_1m`
(`derive/endpoints.go:100-112`), whatever door it came through. So with a site key alone, an
attacker can:

- **Forge server traffic.** Send spans labelled `SERVER` under any service name: add good requests
  to hide a real failure, or bad ones to fire an alert or open a finding. The origin allow-list does
  not stop a script that sets its own header (`apps/receiver/internal/otlp/origin.go`).
- **Push real services past the cap.** The per-tenant cap of 2,000 service-minutes keeps the *first
  in sort order*, not the busiest (`derive/services.go:55,172-237`), so minted names sorted early
  (`aaa-1`, `aaa-2`, …) evict real services from `service_stats_1m` for that minute — and going over
  the cap leaves only a worker log line and an in-process counter
  (`traceassembly/pipeline.go:142-145,756-768`), nothing a screen can show.
- **Forge a check's readings.** The browser door serves `/browser/v1/metrics`, and the synthetic
  check screens read `metrics_gauge` and `logs` purely by metric name and `synthetic.check.*`
  attributes (`apps/api/src/nexovue_api/synthetics/queries.py:30-42,145-322`). Only the prober
  stamps `DOOR_SYNTHETIC` (`apps/scheduler/src/nexovue_scheduler/publish.py:187,249`), but nothing
  checks the stamp, so a site key — or a secret key — can write `synthetic.check.status` readings
  that say a down service is up.

All of this is from reading the code; none of it was run. F3's design traced the same path
(`ai-insights/design.md` row 8), F19's design named it *Forged spans*
(`slo-tracking/design.md:437-458`), and F28 found it independently
(`deployment-release-tracking/status.md:349-365`).

## Intended behavior

The scope the founder approved on 2026-11-02 (foundation item 7; F28 owns it, item 22). It is fixed
and is not redesigned here:

1. Browser-door spans stay out of `service_stats_1m`, `endpoint_stats_1m` and `service_versions_1m`.
   (They still reach `spans`, so the trace view keeps working.)
2. The service cap keeps the busiest.
3. Going over the cap is recorded.
4. `synthetic.*` readings come only from the prober's door.

Out of this change's scope: F3 adds the door to `service_edges_1m` itself (A63), and F28 adds
`service_versions_1m` and the `door` column on `spans`/`logs` (foundation section 8). `service_versions_1m`
does not exist yet — migrations end at `000037` — so item 1 is met by placing the filter at the one
point that will also feed F28's rollup (see `plan.md`, *Current system*).

## Affected specifications and architecture

- The data-plane invariant "metrics derived from all traces before sampling" is untouched; the
  filter removes browser-door spans from the counters, not from sampling or storage.
- F28's Step 4 and F19's Step 4 wait for this change (`deployment-release-tracking/status.md:45`,
  `slo-tracking/status.md:32`). F19's design requires this record to carry its AC37 end-to-end test
  — a forged span through the real receiver and worker — as a condition of its Step 4
  (`slo-tracking/design.md:458`).
- Legitimate browser traffic loses nothing: nexovue's own SDK never sends a `SERVER` span (its one
  page-view span carries no kind, so `INTERNAL` — `packages/browser-sdk/src/rum.ts:68-78`,
  `otlp/spans.ts:9-14`), and `SERVER`/`CONSUMER` are the only kinds the rollups count
  (`derive/endpoints.go:100-102`). Spec D63 (F19): filter by door, not by kind.

## Risks and rollback

- **Risk: the filter lands on the wrong side of a stage** and browser spans vanish from the trace
  view, or keep counting in one table but not the other (F28 D55 warns its completeness check would
  then read "incomplete" every minute). The tests assert both tables and the `spans` row together.
- **Risk: the busiest-first cap breaks drain determinism.** The cap is applied at drain, to rows in
  sorted order, so a replay drains the same block and deduplication recognises it
  (`derive/services.go:166-171`). The new ordering must stay a pure function of the drained rows.
- **Rollback:** pre-deployment, no customers, no live data. Reverting the commit restores today's
  behavior; no data rebuild is needed in either direction (open decision 3 in `status.md`).

## Plan and validation

See [`plan.md`](plan.md) — steps, tasks, files and tests, read from the code. Validation is
`just check` plus the Go module tests with `-count=1`, and the new live end-to-end test against the
dev stack, which must fail loudly when the stack is down.

## Status

See [`status.md`](status.md). Approved 2026-11-07; the founder answered the open
decisions listed there.
