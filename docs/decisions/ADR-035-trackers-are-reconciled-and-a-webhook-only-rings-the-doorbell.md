# ADR-035: Trackers are reconciled, and a webhook only rings the doorbell

## Status

Accepted 2026-11-02, with the F22 design
([`design.md`](../features/case-management/design.md), *Proposed ADRs*, revision 2).
Filed 2026-11-09, when Step 4 built the reconcile (`connections/compare.py`,
`connections/sync.py`). Its webhook half (point 2) was built by Step 5 on 2026-11-09
(`connections/webhooks.py`, `service.ring_from_webhook`), against a simulated GitHub; a
real delivery is F22 T5.2, the founder's after Phase 2.

## Context

A case and its synced tracker issue each hold an open or closed status. Either side can
be unreachable, or written to by someone else at any moment. Neither GitHub's nor Jira's
pages read for the design promise delivery order for their webhooks, Jira drops to one
attempt after 30 failing minutes, and a self-hosted install may not be reachable from
the internet at all (foundation item 9; the manifest's `hook_attributes.active: false`).

An outbox of events, the shape alert delivery uses (ADR-013), suits a stream where every
event must be delivered. An issue's status is a state: only the latest matters.

## Decision

1. **Each synced link stores `seen_state`** (what the tracker last said) **and
   `wanted_state`** (what a person's act wants it to become; null when nothing is
   pending). One function compares them with a fresh read, by one table
   (`connections/compare.py`; design, *The comparison*), and the job applies the row it
   lands on under the finding's and the link's locks, comparing again with what it then
   sees. A person's act that wants what was last seen leaves nothing pending.
2. **A webhook, once its signature is proved, only makes that function run sooner.**
   Nothing in its body changes a case, and it adds no job (D50).
3. **Polling alone must be enough:** an active case's link is read every 5 minutes, a
   resolved one's hourly for 30 days, and then it is not followed (D49, guesses).
4. **No delivery table.** A replay only asks for a read.
5. **Only status crosses,** plus fixed comments, at most one per act that closes the
   issue and one idle warning in a link's case's life (BR10). A comment is committed as
   written before it is posted, so a crash leaves at most one.
6. **Status is not the case.** A close with the case signal open holds the case instead
   of ending it (D73); a reopen another active case blocks needs a person (BR7); a
   reference link never changes a case.

## Consequences

- Duplicates, order and replay stop mattering. An unreachable install works by polling.
- Running the job twice changes nothing: the second pass lands on row 1 (AC45, tested
  row by row, and through the real job against a simulated GitHub).
- It costs a charged read per active link on a timer (12 an hour per active link against
  at least 5,000 per installation, arithmetic), and up to five minutes of delay without a
  webhook.
- It does not suit a feature that needs every change, such as syncing comments. That
  would need its own decision.
- **Unverified until the founder's real-GitHub run after Phase 2** (foundation item 28,
  F22 T4.8): every GitHub answer the reconcile reads was shaped from documentation and
  replayed by `apps/scheduler/tests/tracker_fake.py`.

## Alternatives rejected

- **Webhooks as truth.** Self-hosted may be unreachable, order is not promised, and Jira
  stops retrying.
- **An outbox of status events.** Every intermediate status would be delivered, when only
  the latest matters; its leases and backoff are borrowed, not its order.
- **One-way push.** Fails the plan's "stay in sync": a close in the tracker would never
  reach the case.
- **A delivery table for replay protection.** Protects nothing here, and grows without
  limit.
