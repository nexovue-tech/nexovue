"""What F28 keeps, and for how long (design 3, *Retention*; FR40, D20; plan T4.6).

The expiry sweep (`jobs/expiry.py`) deletes these, in batches, with the rows that
lapse elsewhere:

- **A deploy, at its project's derived retention after it arrived** (`created_at`):
  our clock, never the sender's `deployed_at`, so a back-dated report cannot shorten
  it (AC74).
- **A release, at its project's derived retention after its last activity**
  (`last_activity_at`, also arrival time). Its periods cascade; a deploy still kept
  keeps its row with `release_id` set to null (`ON DELETE SET NULL`), so a release
  lapsing before its deploys cannot fail the sweep.
- **Both, for a deleted project or organization,** once the deletion is older than the
  shortest plan's derived retention. `project_retention` has no row for them, so the
  rule above never would; reads refuse them from the moment of deletion
  (`require_project`).

Each rule first asks the shortest plan's retention, one value, so the arrival-time
index can drive it (`ix_deploy_events_created_at`, `ix_releases_last_activity_at`),
as detection's findings do (`detection/retention.py`).
"""

from __future__ import annotations

from typing import Final

_SHORTEST: Final = "make_interval(days => (SELECT min(derived_retention_days) FROM plans))"


def _lapsed(table: str, arrived: str) -> str:
    """Rows of `table` past retention by `arrived`, or of a project long deleted. Every
    part is a constant of this module."""
    return f"""
    ({arrived} < now() - {_SHORTEST}
     AND {arrived} < now() - make_interval(days => (
         SELECT retention.derived_retention_days FROM project_retention AS retention
         WHERE retention.project_id = {table}.project_id
           AND retention.org_id = {table}.org_id
     )))
    OR EXISTS (
        SELECT 1 FROM projects
        JOIN organizations ON organizations.id = projects.org_id
        WHERE projects.id = {table}.project_id
          AND projects.org_id = {table}.org_id
          AND LEAST(projects.deleted_at, organizations.deleted_at) < now() - {_SHORTEST}
    )"""


# (table, the columns that name a row, what makes a row lapsed), as `jobs/expiry.py`
# reads them. Deploys first: a release's lapse never waits on them, but reading the
# older rows first keeps each batch's work small.
EXPIRED: Final = (
    ("deploy_events", "id", _lapsed("deploy_events", "deploy_events.created_at")),
    ("releases", "id", _lapsed("releases", "releases.last_activity_at")),
)
