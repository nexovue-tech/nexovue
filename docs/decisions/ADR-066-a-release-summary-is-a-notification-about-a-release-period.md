# ADR-066: A release summary is a notification about a release period, put into words at send

## Status

Accepted 2026-11-02 with the F28 design ([`design.md`](../features/deployment-release-tracking/design.md),
section 6; spec D52, D53), and filed 2026-11-12 with F28 Step 5. Amends
[ADR-022](ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md), which named two subjects.

## Context

A project may opt in to one summary of every release, sent to one channel people read, carrying the stored
reading's counts and plain words (F28 FR55). It is a report, not an alert: it never pages, and it opens no
finding.

The outbox could not carry it:

- `names_one_subject` required an incident or a finding unless the kind was a project kind (`0041`, `0050`).
- A project notice is built with `detail={}` from a fixed sentence per kind (`outbox.py`, `messages.py`), so
  counts had no path to the message.
- Every summary on a channel would have named neither subject with `sequence` 0, so the project key
  (`uq_notifications_project_id_channel_id_kind_sequence_project`) would drop the second release's summary.

Detection imports `alerts.outbox` (`detection/notify.py`), and F28's AC42 holds that nothing detection
reaches reads a release table (`test_detection_isolation.py`).

## Decision

1. **A release period is a third subject.** `notifications.release_period_id` cascades with its period, as
   `incident_id` and `finding_id` do. `names_one_subject` counts it, a check holds that a `release_summary`
   row, and only one, names a period, and its own unique key with `sequence` always 0 makes a retried job
   write nothing twice. The project key's condition excludes rows naming a period (`0074`, `0075`).
2. **The opt-in is one column of the project.** `projects.release_summary_channel_id`, `ON DELETE SET NULL`,
   null by default, set under `CONFIGURE_PROJECTS` through `PUT /projects/{project_id}/release-summary`, which
   refuses another organization's channel and any kind not read by people (D53).
3. **The words are read at send, never frozen into the row** (D52). The job stores the reading, which holds
   no names, on the period; the version names are read when the notice is sent.
4. **The claim does not read the release tables.** It returns the period's id; delivery reads the release,
   its versions and the stored reading after the claim commits, on a connection of its own, as FR44's line
   is read (D51). If that read fails, nothing is sent and the batch is tried again, recorded in the delivery
   log. This keeps AC42: the outbox, which detection imports, names no release table.
5. **It is never an alert.** It goes only to a channel people read, is coloured neutral, carries no severity
   tag, and opens nothing. The row's severity is `warning`, the lower of the two the outbox has, so quiet
   hours hold it as a digest.

## Consequences

- ADR-013's rules hold for the new subject: the row is written by the job that decided it, claimed under a
  lease, and each period's messages go in order (`_IN_ORDER` matches on the period too).
- A summary in a digest mixed with alerts is one line, counted among the alerts. A digest of summaries alone
  reads "3 release summaries". Both are stated limits.
- **Rejected: a project-kind notice with counts in a payload column.** It would freeze words at write time,
  and a second release on one channel would collide with the first.
- **Rejected: joining the release tables in the claim (`_TAKEN`)**, as the design first wrote it. Detection
  imports the outbox, so the join would break AC42, and a failing read inside the claim's one transaction
  would hold every notice it claimed.
