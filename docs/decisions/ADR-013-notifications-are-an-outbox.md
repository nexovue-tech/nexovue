# ADR-013: Notifications are an outbox, written in the transaction that decided them

## Status

Accepted 2026-09-19, with the F21 design.

## Context

F21's spec requires that a notification decided is never lost to a crash (NFR2) and never sent twice for one
incident on one channel (NFR3), and that a slow or failing receiver cannot delay evaluation (NFR5). Receivers are
the customer's — Slack, Teams, Discord, PagerDuty, email, arbitrary webhooks — and fail, rate-limit and hang in
ways we do not control.

The check-state decision is made inside `state_store._apply`'s transaction. The sweeps make theirs in their own.

## Decision

1. Deciding to notify **writes a `notifications` row in the same transaction** as the incident change that caused
   it. Nothing is sent inside that transaction.
2. A unique key on `(incident_id, channel_id, kind, sequence)` makes a second *opened* for one incident on one
   channel impossible at the database.
3. Delivery claims due rows with `FOR UPDATE SKIP LOCKED` and a lease. A `deliver` task is dispatched after the
   commit (fast path); a 15-second sweep claims anything the fast path missed (safety net). A claim takes **one
   channel**, so its lease starts just before that channel's send, and an outcome is recorded only against rows
   that still carry the claim's own lease — a worker that outlived its lease cannot undo the claim that replaced
   it (found in review, 2026-09-19: ten channels claimed at once let the last leases lapse mid-run).
   Within one incident on one channel, rows go **in the order they were decided**: a row waits while an earlier
   one is waiting on a retry, so a receiver never hears *resolved* before *opened*.
4. Grouping, the burst cap, quiet hours and digests are applied **at delivery**, where every due row for one
   channel is visible together — not at decision time.
5. Every attempt is recorded in `notification_attempts`: the customer's delivery log.

## Rejected

- **Send inside the deciding transaction.** A slow receiver holds a database transaction and the check-state row
  lock; a failed send could roll back a recorded state change.
- **Dispatch a Celery task carrying the message, no table.** The task is lost if the process dies between commit
  and dispatch, or with Redis; there is no delivery log to show; and deduplication would have to live somewhere
  anyway.
- **Kafka as the outbox.** Right for telemetry volume, wrong for this: tens of rows a minute, each needing
  per-channel state, a lease and a queryable history — which is a table.

## Consequences

- **At-least-once, with one named duplicate**: a crash between a receiver accepting a message and our recording it
  delivers it again when the lease ends. PagerDuty collapses it by `dedup_key` (the incident id) and webhook
  receivers by `webhook-id` (the batch id, the same on every retry); Slack, Teams and Discord would show it twice. Email sends
  under a Resend idempotency key per batch and address, so a retry of a batch that reached some addresses does
  not mail them again.
- Latency is the fast path's — seconds — except where the design deliberately waits (the 30-second grouping window
  for check incidents, quiet hours for warnings).
- `notification_attempts` grows with deliveries and is pruned at 30 days.
