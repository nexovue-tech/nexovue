# ADR-038: A burn does not repeat news, decided at the claim and released when its reason ends

## Status

Accepted 2026-11-02 with the F19 design ([`design.md`](../features/slo-tracking/design.md), *Proposed ADRs*,
the third), which the founder approved; filed with its code, 2026-11-09 (F19 plan Step 5, Alembic `0063` and
`0064`). The number is the next free one at filing, above parallel work's; the coordinator may renumber it at
commit.

## Context

One problem can raise a reliability target's burn, an anomaly finding and a threshold incident on one service.
Before this, only PagerDuty shared one alert between linked subjects (D29, D35); every channel people read got a
message for each. Holding messages back is the riskiest change F19 makes, because delivery is a path F6 has tested
closely, and an alert that is never sent is worse than one sent twice.

## Decision

At `outbox.claim`, on a channel people read, a news row is recorded as `dropped`, with `withheld_why = same_news`
and the subject that withheld it, when a burn is one of the two subjects, the other is the same news by FR21's
table (same organization and project), still open, a burn as the other judged within 15 minutes, and it reached
the channel within the last 60 minutes at the same or a higher severity, or is due beside it now and wins the tie.
An ending is dropped only where no news of its subject reached the channel. A burn's rows wait out its service's
snooze (`service_snoozed`) on people's channels, and its news on PagerDuty. When nothing withholds a subject any
more and it is still open, one `opened` row goes to that channel, at its current severity, in the same claim as the
other's ending where there is one; the delivery sweep does the same across channels for endings that write no row.
Webhooks are never touched.

## Why

Deciding at the claim means nothing waits: an alert never stalls to see whether another arrives. Every decision
is a row in the delivery log with its reason in words and in structure, so the release is a query, not text
parsing, and nothing withheld is lost. Limiting it to subjects where a burn is involved leaves F6's and F21's
behaviour exactly as tested (AC29, AC95).

## Consequences

- `notifications` gains `withheld_why`, `withheld_by_incident_id`, `withheld_by_finding_id` and `released_at`
  (`0063`), and a partial index over withheld rows not yet released (`0064`).
- The outbox reads `service_detection_settings`: a snooze belongs to the service, not to a rule.
- A finding and a threshold incident with no burn still send two messages; F21 can later drop the burn condition
  to fix that too.
- Claim time grows by one guarded lookup on channels with no target; AC50 measures it (T6.2).

## Rejected

- Holding a message for a while to merge it with the next: an alert would wait.
- Deciding when the row is written: the other subject's delivery is not known then.
- Widening the rule to every pair of subjects now: changes F6's tested behaviour beyond F19's scope.
