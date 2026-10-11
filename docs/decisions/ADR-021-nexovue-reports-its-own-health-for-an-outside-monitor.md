# ADR-021: nexovue reports its own health for an outside monitor to read

## Status

Accepted 2026-10-11 — explicit founder decision, readiness group 7
(`docs/changes/we-can-see-ourselves/`). Built and verified on the development stack the same day.
Which monitor reads these endpoints is decided with the hosting target (group 8).

## Context

Before this, nothing told anyone nexovue was down. The scheduler's three services had no health check
at all, so a beat whose loop had hung kept its container up while every customer's checks stopped. No
service exported a number a monitor could read: the counts existed only inside log lines. Consumer lag
and dead-letter depth were not measured anywhere, and the console dropped every error in production.

Decision 0053 (2026-08-26, deferred) set three rules for nexovue monitoring itself: the "nexovue is
down" alert comes from outside nexovue; structured logs go to stdout independently of the telemetry
pipeline; and nexovue's own telemetry is an organization of its own. It was deferred until production.

## Decision

1. **Pull, not push.** Every service answers `/healthz` (alive), `/readyz` (able to do its job) and
   `/metrics` (the Prometheus text format) on an internal port — the Go services' admin listener, the
   scheduler's `SCHEDULER_HEALTH_ADDR`, the API's root. No edge routes any of them: the edge sends the
   API `/api/*` only, which a test holds. A monitor outside nexovue reads them.
2. **Liveness means the work is moving, not that a process answers.** The scheduler's `/healthz`
   fails when its loop has been silent for 30 s; the worker's when a pipeline has stopped. Every
   long-running service in both deployment files has a healthcheck.
3. **Measured on the source of truth.** Consumer lag is the topics' end offsets minus each group's
   committed offsets, and dead-letter depth the retained records in each `*-dlq` topic, both read from
   the broker by `kadm` when scraped. Queue depth is the scheduler's own predicates over its tables.
4. **Bounded labels.** No label is a tenant, project, path or anything a request chose: route groups
   come from the matched route's template, reasons and signals from fixed lists, groups and topics
   from configuration.
5. **The console's errors go to the API's log**, as structured lines (`source=browser`), not into a
   nexovue organization.

## Consequences

- Decision 0053's rules 1 and 2 are met: the endpoints are read from outside, and the console's errors
  join the stdout logs. **Rule 3 stays deferred**: nexovue's telemetry is not sent into a nexovue
  organization. A collector reading these same endpoints can add that later without changing them.
- A stopped worker cannot report its own lag; its `/metrics` stops answering instead, and a monitor
  alerts on the missing scrape. `monitoring.md` names that and every other threshold.
- Docker marks an unhealthy container and does not restart it; acting on the signal is the monitor's
  job, or the orchestrator's (group 8).

## Rejected

- **Push into a nexovue organization** (0053 rule 3 now). A monitor inside the thing it monitors goes
  dark exactly when it is needed.
- **The OpenTelemetry metrics SDK with a Prometheus exporter.** One more layer for the same text on
  the wire, with scope labels to strip; the Prometheus clients are the format's own.
- **The console's errors through the browser SDK into a nexovue organization.** It needs an
  organization nexovue does not have, and fails when the telemetry pipeline is what broke.
