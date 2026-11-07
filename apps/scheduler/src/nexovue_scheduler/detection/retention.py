"""What detection keeps, and for how long (C24; design revision 2.1, security m4).

The expiry sweep (`jobs/expiry.py`) deletes these, in batches, with the rows that
lapse elsewhere:

- **Closed findings, at their project's derived retention.** A finding keeps
  telemetry text, service and endpoint names in its title and signals, and must
  not outlive the telemetry it names. Its notifications and incident links go
  with it (they cascade). An open finding stays, however old: it is the
  customer's current problem.
- **A case keeps its finding (F22 FR24, FR25).** A closed finding whose case is
  still being investigated stays, or the work would vanish on a plan clock; a
  resolved case's finding goes once retention has passed after the later of
  `closed_at` and `case_resolved_at`. Its notes, events and owed emails cascade.
  A case cannot pin names for ever, because an idle one ends itself (BR13).
- **A joined finding keeps its own clock** (D43), even while its root's case is
  active: there is no clause on the root, so one case cannot pin an unbounded set
  of rows. `root_finding_id` is `SET NULL`, so a root may go before the findings
  that joined it, and they stand alone.
- **`intake_refusals`, `detection_events` and `baseline_shifts`, once the
  baseline job no longer reads back to them** (`baselines.project_exclusions`,
  `project_shifts`): refused and held minutes, and shifted days, are kept out
  of or moved in every normal built over that history.
"""

from __future__ import annotations

from typing import Final

from nexovue_scheduler.detection.baselines import DAY, HISTORY

READ_BACK: Final = HISTORY + DAY
_READ_BACK_PASSED: Final = f"now() - interval '{READ_BACK.days} days'"

# The project's own derived retention, from the one definition of it (migration 0005).
# A project or organization being deleted has none here; its deletion removes these.
# The shortest plan's retention comes first, unchanged: it is one value, so it can
# drive `ix_findings_closed_at`, where the per-project one cannot, and `closed_at` is
# never after the later date below, so it misses no row the rest would delete.
# `GREATEST` ignores a null `case_resolved_at`, so a finding with no case keeps its
# rule. Rows kept by the case match nothing, so the sweep's batches still end.
_PAST_DERIVED_RETENTION: Final = """
    state = 'closed'
    AND closed_at < now() - make_interval(days => (SELECT min(derived_retention_days) FROM plans))
    AND GREATEST(closed_at, case_resolved_at) < now() - make_interval(days => (
        SELECT retention.derived_retention_days FROM project_retention AS retention
        WHERE retention.project_id = findings.project_id
          AND retention.org_id = findings.org_id
    ))
    AND case_state IS DISTINCT FROM 'investigating'"""

# (table, the columns that name a row, what makes a row lapsed), as `jobs/expiry.py`
# reads them.
EXPIRED: Final = (
    ("intake_refusals", "minute, receiver_id, reason", f"minute < {_READ_BACK_PASSED}"),
    ("detection_events", "id", f"at < {_READ_BACK_PASSED}"),
    (
        "baseline_shifts",
        "id",
        f"from_minute < {_READ_BACK_PASSED} AND created_at < {_READ_BACK_PASSED}",
    ),
    ("findings", "id", _PAST_DERIVED_RETENTION),
)
