"""Claiming due notifications and recording what delivering them did (ADR-013).

**A claim is a lease, not a lock held across the network.** Rows are marked
`delivering` with a lease in one short transaction; the message is sent outside
any transaction; the outcome is recorded in another. A worker that dies between
the two leaves its rows to be claimed again when the lease ends — the one
duplicate the design allows, which `webhook-id` and PagerDuty's `dedup_key` let a
receiver collapse.

**At most one batch in flight per channel.** A claim locks the channel rows it
takes (`SKIP LOCKED`), and a channel with a live lease is not offered again, so
two workers never talk to one receiver at once and a slow receiver holds one
delivery slot rather than all of them (NFR5).

**What people read is shaped here, where everything due for one channel is visible
together** (design, *Shaping at delivery time*). For email, Slack, Teams and
Discord, every row due at once goes as one message — which is what makes a shared
outage one message (FR13), a quiet-hours digest one message (FR15), and a burst
past ten a minute one summary (FR12). Warnings wait out quiet hours; criticals
never do (BR4). PagerDuty and webhooks are read by machines that key on each
subject, so they get one event per notification and are not shaped.

**A notification has one subject** (ADR-022): an incident, a finding, or — for
detection stopping and running again (D24) — the project itself. The claim reads
each through one `Subject`, so everything after it treats them alike.

**One problem is one PagerDuty alert, whichever fires first** (D29, D35). A
finding and an incident linked on one service share the alert of whichever
reached the channel first; the other's triggers are notes on it. Each row's key
is fixed at its subject's first claim there and kept, and the alert is resolved
only by the last of them to end: PagerDuty drops events for a resolved alert and
opens a new one on the next trigger, so an early resolve would page again.

**A burn does not repeat news** (F19 FR20 to FR26, ADR-038). On a channel people read,
a news row of a subject is `dropped`, with why, when a reliability target's burn is
involved and another subject, the same news, still open, told the channel within the
hour at the same or a higher severity. A burn's messages wait out its service's snooze.
When that reason ends and the subject is still open, one message goes after all. With
no burn involved, nothing here changes what is sent.

**These statements read across organizations on purpose.** Delivery serves every
tenant from one queue; each row carries its own `org_id`, and every write back
names it.
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import (
    ENDING_KINDS,
    NEWS_KINDS,
    SEVERITY_RANK,
    WITHHELD_SAME_NEWS,
    WITHHELD_SERVICE_SNOOZED,
)
from nexovue_platform.alerts.quiet_hours import parse_quiet_hours, quiet_until
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Long enough for one send's 10-second timeout and its bookkeeping; short enough
# that a dead worker's notification is late by a minute, not an hour.
LEASE: Final = timedelta(seconds=60)

# Retried for up to an hour from the first attempt (FR19), then final.
RETRY_WINDOW: Final = timedelta(hours=1)
FIRST_RETRY_SECONDS: Final = 30
LONGEST_RETRY_SECONDS: Final = 600
# A receiver's own `Retry-After` is honoured, up to the end of the window.
LONGEST_RETRY_AFTER_SECONDS: Final = 3600

MUTED_CHANNEL_REASON: Final = "the channel was muted"
ALERT_STILL_OPEN_REASON: Final = (
    "not sent: the PagerDuty alert stays open for a linked problem that is still open"
)

# The channel whose alerts one problem shares (D35). A webhook keys on each subject.
PAGERDUTY: Final = "pagerduty"

# F19 FR22: a channel told the same news within this long withholds a new row (a guess,
# in the spec's register). It stops new rows being withheld; it releases nothing.
SAME_NEWS_WINDOW: Final = timedelta(minutes=60)
# A burn withholds only while a tier could judge it this recently, so a frozen burn
# silences nothing (D56): its position's `evaluated_at` is the last minute one could.
BURN_FRESH_FOR: Final = timedelta(minutes=15)
SAME_NEWS_REASON: Final = "not sent: this channel was told about this within the hour"
NOTHING_TOLD_REASON: Final = (
    "not sent: this channel was never told about it, because the same news reached it first"
)
SNOOZED_REASON: Final = "not sent: the service is snoozed"

# A finding's notices are routed by rules of this kind; a project's are detection's own.
ANOMALY_FINDING: Final = "anomaly_finding"
DETECTION: Final = "detection"

# FR12: at most this many messages a minute to one channel; the rest wait and go
# together as one.
BURST_LIMIT: Final = 10
BURST_WINDOW: Final = timedelta(minutes=1)

# The most rows one message carries. A digest past this is sent in parts.
MOST_IN_ONE_MESSAGE: Final = 50

_DUE: Final = """
    (notifications.state = 'pending' AND notifications.deliver_after <= :now)
    OR (notifications.state = 'delivering' AND notifications.lease_until < :now)
"""

# A notification waits while an earlier one of the same subject on the same
# channel waits on a retry, so *resolved* never overtakes *opened*. Rows only held
# by quiet hours or the burst cap (never attempted) do not block: a critical must
# not wait for a held warning. A project's own notices name neither subject, so
# the project and both nulls make their subject.
_IN_ORDER: Final = """
    NOT EXISTS (
        SELECT 1 FROM notifications AS earlier
        WHERE earlier.org_id = notifications.org_id
          AND earlier.channel_id = notifications.channel_id
          AND earlier.project_id = notifications.project_id
          AND earlier.incident_id IS NOT DISTINCT FROM notifications.incident_id
          AND earlier.finding_id IS NOT DISTINCT FROM notifications.finding_id
          AND earlier.created_at < notifications.created_at
          AND earlier.attempts > 0
          AND earlier.state IN ('pending', 'delivering')
    )
"""

_CLAIMABLE_CHANNELS: Final = text(
    f"""
    SELECT id, org_id, kind, name, config, sealed_secret, muted_until, quiet_hours
    FROM notification_channels
    WHERE EXISTS (
            SELECT 1 FROM notifications
            WHERE notifications.channel_id = notification_channels.id AND ({_DUE})
              AND {_IN_ORDER}
        )
      AND NOT EXISTS (
            SELECT 1 FROM notifications
            WHERE notifications.channel_id = notification_channels.id
              AND notifications.state = 'delivering' AND notifications.lease_until >= :now
        )
      AND (CAST(:org_id AS uuid) IS NULL OR org_id = CAST(:org_id AS uuid))
    ORDER BY id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)
_DROP_DUE: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL
    WHERE org_id = :org_id AND channel_id = :channel_id AND ({_DUE})
    """
)
_OLDEST_DUE: Final = text(
    f"""
    SELECT id, batch_id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND ({_DUE}) AND {_IN_ORDER}
    ORDER BY created_at, id
    LIMIT 1
    """
)
_ALL_DUE: Final = text(
    f"""
    SELECT id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id IS NULL AND ({_DUE})
      AND {_IN_ORDER}
    ORDER BY created_at, id
    LIMIT {MOST_IN_ONE_MESSAGE}
    """
)
_HOLD_WARNINGS: Final = text(
    f"""
    UPDATE notifications SET deliver_after = :until
    WHERE org_id = :org_id AND channel_id = :channel_id AND severity = 'warning'
      AND batch_id IS NULL AND ({_DUE})
    """
)
_HOLD_ALL: Final = text(
    f"""
    UPDATE notifications SET deliver_after = :until
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id IS NULL AND ({_DUE})
    """
)
_RECENT_MESSAGES: Final = text(
    """
    SELECT count(DISTINCT batch_id), min(attempted_at) FROM notification_attempts
    WHERE org_id = :org_id AND channel_id = :channel_id AND attempted_at > :since
    """
)
_DUE_IN_BATCH: Final = text(
    f"""
    SELECT id FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id = :batch_id AND ({_DUE})
    """
)
_TAKE: Final = text(
    """
    UPDATE notifications
    SET state = 'delivering', lease_until = :lease_until, batch_id = :batch_id
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[]))
    """
)
# A subject's own PagerDuty key: the kind leads, so a finding and an incident never
# share one by accident.
_OWN_KEY: Final = """
    CASE WHEN notifications.incident_id IS NOT NULL
             THEN 'incident:' || CAST(notifications.incident_id AS text)
         WHEN notifications.finding_id IS NOT NULL
             THEN 'finding:' || CAST(notifications.finding_id AS text)
         ELSE 'project:' || CAST(notifications.project_id AS text)
    END
"""


_NEWS: Final = ", ".join(f"'{kind}'" for kind in sorted(NEWS_KINDS))


def _rank(severity: str) -> str:
    """A severity column as `SEVERITY_RANK`'s number, so SQL can say "at least"."""
    cases = " ".join(f"WHEN '{name}' THEN {rank}" for name, rank in SEVERITY_RANK.items())
    return f"(CASE {severity} {cases} ELSE 0 END)"


def _reached(row: str) -> str:
    """The row got to its channel or is on its way: a row waiting on a retry has a
    future `deliver_after` and still counts (FR22, AC57)."""
    return (
        f"({row}.state IN ('delivered', 'delivering') "
        f"OR ({row}.state = 'pending' AND {row}.attempts > 0))"
    )


def _same_news(xi: str, xf: str, yi: str, yf: str) -> str:
    """FR21's table: subject X (incident `xi` or finding `xf`, one of them null) and
    subject Y are the same news, in either order, with a burn (`about_signal` set) as
    one of them. Same organization and project are the caller's join."""

    def burn_and_finding(burn: str, finding: str) -> str:
        return f"""(
            {burn}.about_signal IS NOT NULL AND {finding}.id IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM finding_incidents AS link
                WHERE link.org_id = {burn}.org_id AND link.incident_id = {burn}.id
                  AND link.finding_id = {finding}.id)
            AND {finding}.signals @> jsonb_build_array(jsonb_build_object('signal',
                CASE {burn}.about_signal WHEN 'latency' THEN 'slower' ELSE 'failing' END))
        )"""

    def burn_and_threshold(burn: str, other: str) -> str:
        return f"""(
            {burn}.about_signal IS NOT NULL AND {other}.subject_key = {burn}.about_key
            AND {other}.condition_kind = CASE {burn}.about_signal
                WHEN 'latency' THEN 'service_latency_p95' ELSE 'service_error_rate' END
        )"""

    return f"""(
        {burn_and_finding(xi, yf)} OR {burn_and_finding(yi, xf)}
        OR {burn_and_threshold(xi, yi)} OR {burn_and_threshold(yi, xi)}
        -- Two burns: one target under two rules, or one service and type unless each
        -- names a different endpoint.
        OR ({xi}.about_signal IS NOT NULL AND {yi}.about_signal IS NOT NULL
            AND {xi}.id <> {yi}.id
            AND ({xi}.subject_key = {yi}.subject_key
                 OR ({xi}.about_key = {yi}.about_key AND {xi}.about_signal = {yi}.about_signal
                     AND NOT ({xi}.about_endpoint IS NOT NULL
                              AND {yi}.about_endpoint IS NOT NULL
                              AND {xi}.about_endpoint <> {yi}.about_endpoint))))
        -- A check burn beside its check's incident: only the burn is ever withheld.
        OR ({xi}.about_check_id IS NOT NULL AND {yi}.condition_kind = 'check_state'
            AND {yi}.subject_key = 'check:' || CAST({xi}.about_check_id AS text))
    )"""


def _open_and_judged(yi: str, yf: str) -> str:
    """Y is still open, and a burn as Y was judged by a tier within `BURN_FRESH_FOR`."""
    return f"""(
        (({yi}.id IS NOT NULL AND {yi}.resolved_at IS NULL) OR {yf}.state = 'open')
        AND ({yi}.about_signal IS NULL OR EXISTS (
            SELECT 1 FROM alert_evaluations AS judged
            WHERE judged.org_id = {yi}.org_id AND judged.rule_id = {yi}.rule_id
              AND judged.subject_key = {yi}.subject_key
              AND judged.evaluated_at > :fresh_since))
    )"""


def _snoozed(burn: str) -> str:
    """The service a burn watches is snoozed now (FR26). Detection's own setting, read
    here on purpose: the snooze belongs to the service, not to a rule."""
    return f"""EXISTS (
        SELECT 1 FROM service_detection_settings AS service
        WHERE service.org_id = {burn}.org_id AND service.project_id = {burn}.project_id
          AND {burn}.about_key = 'service:' || service.service_name
          AND service.snoozed_until > :now
    )"""


def _open_for_another(key: str) -> str:
    """Whether the alert `key` names on this row's channel still carries a subject
    other than this row's that has not ended."""
    return f"""
    EXISTS (
        SELECT 1 FROM notifications AS member
        LEFT JOIN incidents
            ON incidents.id = member.incident_id AND incidents.org_id = member.org_id
        LEFT JOIN findings
            ON findings.id = member.finding_id AND findings.org_id = member.org_id
        WHERE member.org_id = notifications.org_id
          AND member.channel_id = notifications.channel_id
          AND member.dedup_key = {key}
          AND NOT (member.incident_id IS NOT DISTINCT FROM notifications.incident_id
                   AND member.finding_id IS NOT DISTINCT FROM notifications.finding_id)
          AND ((member.incident_id IS NOT NULL AND incidents.resolved_at IS NULL)
               OR findings.state = 'open')
    )
    """


_ENDING: Final = ", ".join(f"'{kind}'" for kind in sorted(ENDING_KINDS))
# A burn's news shares an alert sent at the same or a higher severity, so a critical
# burn after a warning alert pages rather than becoming a note (F19 FR24a, AC27).
_HOST_SEVERE_ENOUGH: Final = f"{_rank('host.severity')} >= {_rank('notifications.severity')}"
# Threshold incidents a burn's alert can carry, and be carried by (F19 FR24a).
_SHARES_WITH_BURNS: Final = "'service_error_rate', 'service_latency_p95'"
# Fixed once per subject and channel: the key its earlier rows went under; else, for
# news rather than an ending, the alert of a linked subject that is still open
# there (either order, D35); else, for a reliability target's burn, the alert of an
# open subject about the same service or check (F19 FR24a); else its own. Linked
# through a finding, two incidents share the first one's alert too, because the
# finding's rows carry that key. `service_silent` and `check_state` openings never
# join a burn's alert.
_PIN_KEY: Final = text(
    f"""
    UPDATE notifications SET dedup_key = coalesce(
        (
            SELECT earlier.dedup_key FROM notifications AS earlier
            WHERE earlier.org_id = notifications.org_id
              AND earlier.channel_id = notifications.channel_id
              AND (earlier.incident_id = notifications.incident_id
                   OR earlier.finding_id = notifications.finding_id)
              AND earlier.dedup_key IS NOT NULL
            LIMIT 1
        ),
        (
            SELECT host.dedup_key
            FROM finding_incidents AS link
            JOIN notifications AS host
                ON host.org_id = link.org_id
               AND host.channel_id = notifications.channel_id
               AND ((link.finding_id = notifications.finding_id
                     AND host.incident_id = link.incident_id)
                    OR (link.incident_id = notifications.incident_id
                        AND host.finding_id = link.finding_id))
            WHERE notifications.kind NOT IN ({_ENDING})
              AND link.org_id = notifications.org_id
              AND (link.finding_id = notifications.finding_id
                   OR link.incident_id = notifications.incident_id)
              AND host.dedup_key IS NOT NULL
              AND {_open_for_another("host.dedup_key")}
              AND ({_HOST_SEVERE_ENOUGH} OR NOT EXISTS (
                  SELECT 1 FROM incidents AS burn
                  WHERE burn.org_id = notifications.org_id AND burn.about_signal IS NOT NULL
                    AND burn.id IN (notifications.incident_id, host.incident_id)))
            ORDER BY host.created_at, host.id
            LIMIT 1
        ),
        (
            SELECT host.dedup_key
            FROM incidents AS mine
            JOIN notifications AS host
                ON host.org_id = mine.org_id AND host.channel_id = notifications.channel_id
               AND host.incident_id IS NOT NULL AND host.incident_id <> mine.id
            JOIN incidents AS other
                ON other.id = host.incident_id AND other.org_id = host.org_id
               AND other.project_id = mine.project_id
            WHERE notifications.kind NOT IN ({_ENDING})
              AND mine.org_id = notifications.org_id AND mine.id = notifications.incident_id
              AND host.dedup_key IS NOT NULL
              AND {_HOST_SEVERE_ENOUGH}
              AND {_open_for_another("host.dedup_key")}
              AND (
                  -- The row is a burn's: another burn about the same thing, a threshold
                  -- incident on its service, or its check's incident.
                  (mine.about_signal IS NOT NULL
                   AND ((other.about_signal IS NOT NULL AND other.about_key = mine.about_key)
                        OR (other.condition_kind IN ({_SHARES_WITH_BURNS})
                            AND other.subject_key = mine.about_key)
                        OR (other.condition_kind = 'check_state'
                            AND other.subject_key = 'check:' || CAST(mine.about_check_id AS text))))
                  -- The row is a threshold incident's: a burn about its service.
                  OR (mine.condition_kind IN ({_SHARES_WITH_BURNS})
                      AND other.about_signal IS NOT NULL AND other.about_key = mine.subject_key)
              )
            ORDER BY host.created_at, host.id
            LIMIT 1
        ),
        {_OWN_KEY}
    )
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND dedup_key IS NULL
    """
)
# An ending sent while the alert still carries another open subject would resolve
# that subject's alert too; the last of them to end resolves it instead.
_WITHHOLD_ENDING: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[]))
      AND kind IN ({_ENDING})
      AND {_open_for_another("notifications.dedup_key")}
    RETURNING id
    """
)

# ------------------------------------------------- what a burn may withhold (F19)
#
# At a claim of a channel people read or PagerDuty, in this order: release, snooze,
# withhold (design, *What a burn may withhold*). Webhooks are untouched by all three.

# Whether any of it can apply to what is due here: a due row in a project holding an
# open burn, a burn's own row, or a row whose subject has a row withheld on this
# channel. A channel with no target pays for this lookup alone.
_BURN_INVOLVED: Final = text(
    f"""
    SELECT EXISTS (
        SELECT 1 FROM notifications
        WHERE notifications.org_id = :org_id AND notifications.channel_id = :channel_id
          AND notifications.batch_id IS NULL AND ({_DUE})
          AND (EXISTS (
                  SELECT 1 FROM incidents AS burn
                  WHERE burn.org_id = notifications.org_id
                    AND burn.project_id = notifications.project_id
                    AND burn.about_signal IS NOT NULL
                    AND (burn.resolved_at IS NULL OR burn.id = notifications.incident_id))
               OR EXISTS (
                  SELECT 1 FROM notifications AS held
                  WHERE held.org_id = notifications.org_id
                    AND held.channel_id = notifications.channel_id
                    AND held.withheld_why IS NOT NULL
                    AND (held.incident_id = notifications.incident_id
                         OR held.finding_id = notifications.finding_id)))
    )
    """
)
# FR26: a burn's due row, on a channel people read, or a news row on PagerDuty (a page
# wakes a person; an ending closes what was opened), waits out its service's snooze.
_DROP_SNOOZED: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL,
        withheld_why = '{WITHHELD_SERVICE_SNOOZED}'
    FROM incidents AS burn
    WHERE notifications.org_id = :org_id AND notifications.channel_id = :channel_id
      AND notifications.batch_id IS NULL AND ({_DUE})
      AND (CAST(:people AS boolean) OR notifications.kind IN ({_NEWS}))
      AND burn.org_id = notifications.org_id AND burn.id = notifications.incident_id
      AND burn.about_signal IS NOT NULL AND {_snoozed("burn")}
    RETURNING notifications.id
    """
)
# FR22: a due news row X, not in a batch, whose same news Y is open (a burn as Y judged
# within 15 minutes) and either reached this channel within the hour at the same or a
# higher severity, or is due beside it now and wins the tie: a finding or threshold
# row over a burn's, and between two burns the older row.
_WITHHOLD_SECOND: Final = text(
    f"""
    WITH found AS (
        SELECT DISTINCT ON (x.id) x.id, y.incident_id AS by_incident, y.finding_id AS by_finding
        FROM notifications AS x
        LEFT JOIN incidents AS xi ON xi.id = x.incident_id AND xi.org_id = x.org_id
        LEFT JOIN findings AS xf ON xf.id = x.finding_id AND xf.org_id = x.org_id
        JOIN notifications AS y
            ON y.org_id = x.org_id AND y.channel_id = x.channel_id
           AND y.project_id = x.project_id AND y.kind IN ({_NEWS})
           AND (y.incident_id IS DISTINCT FROM x.incident_id
                OR y.finding_id IS DISTINCT FROM x.finding_id)
        LEFT JOIN incidents AS yi ON yi.id = y.incident_id AND yi.org_id = y.org_id
        LEFT JOIN findings AS yf ON yf.id = y.finding_id AND yf.org_id = y.org_id
        WHERE x.org_id = :org_id AND x.channel_id = :channel_id AND x.batch_id IS NULL
          AND x.kind IN ({_NEWS})
          AND ((x.state = 'pending' AND x.deliver_after <= :now)
               OR (x.state = 'delivering' AND x.lease_until < :now))
          AND (xi.about_signal IS NOT NULL OR yi.about_signal IS NOT NULL)
          AND {_rank("y.severity")} >= {_rank("x.severity")}
          AND {_open_and_judged("yi", "yf")}
          AND {_same_news("xi", "xf", "yi", "yf")}
          AND (({_reached("y")} AND coalesce(y.delivered_at, :now) > :told_since)
               OR (y.state = 'pending' AND y.attempts = 0 AND y.batch_id IS NULL
                   AND y.deliver_after <= :now
                   AND (yi.about_signal IS NULL
                        OR (xi.about_signal IS NOT NULL
                            AND (y.created_at, y.id) < (x.created_at, x.id)))))
        ORDER BY x.id, y.created_at, y.id
    )
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL,
        withheld_why = '{WITHHELD_SAME_NEWS}',
        withheld_by_incident_id = found.by_incident, withheld_by_finding_id = found.by_finding
    FROM found
    WHERE notifications.org_id = :org_id AND notifications.id = found.id
    RETURNING notifications.id
    """
)
# FR24: an ending is owed only where a news row of its subject reached the channel. A
# subject with nothing withheld here is untouched, which keeps F6's behaviour.
_WITHHOLD_UNTOLD_ENDING: Final = text(
    f"""
    UPDATE notifications
    SET state = 'dropped', outcome_reason = :reason, lease_until = NULL
    WHERE org_id = :org_id AND channel_id = :channel_id AND batch_id IS NULL AND ({_DUE})
      AND kind IN ({_ENDING})
      AND EXISTS (
          SELECT 1 FROM notifications AS held
          WHERE held.org_id = notifications.org_id
            AND held.channel_id = notifications.channel_id
            AND held.incident_id IS NOT DISTINCT FROM notifications.incident_id
            AND held.finding_id IS NOT DISTINCT FROM notifications.finding_id
            AND held.withheld_why IS NOT NULL)
      AND NOT EXISTS (
          SELECT 1 FROM notifications AS news
          WHERE news.org_id = notifications.org_id
            AND news.channel_id = notifications.channel_id
            AND news.incident_id IS NOT DISTINCT FROM notifications.incident_id
            AND news.finding_id IS NOT DISTINCT FROM notifications.finding_id
            AND news.kind IN ({_NEWS}) AND {_reached("news")})
    RETURNING id
    """
)
# FR23: each subject with rows withheld on this channel and not yet released, and
# whether anything still holds any of them back: for `same_news`, Y open, judged and
# told here at any age; for `service_snoozed`, the snooze.
_RELEASABLE: Final = text(
    f"""
    SELECT held.project_id, held.incident_id, held.finding_id,
           array_agg(held.id) AS ids,
           bool_or(CASE held.withheld_why
               WHEN '{WITHHELD_SAME_NEWS}' THEN EXISTS (
                   SELECT 1 FROM notifications AS y
                   LEFT JOIN incidents AS yi ON yi.id = y.incident_id AND yi.org_id = y.org_id
                   LEFT JOIN findings AS yf ON yf.id = y.finding_id AND yf.org_id = y.org_id
                   WHERE y.org_id = held.org_id AND y.channel_id = held.channel_id
                     AND y.project_id = held.project_id AND y.kind IN ({_NEWS})
                     AND (y.incident_id IS DISTINCT FROM held.incident_id
                          OR y.finding_id IS DISTINCT FROM held.finding_id)
                     AND {_reached("y")}
                     AND {_open_and_judged("yi", "yf")}
                     AND {_same_news("xi", "xf", "yi", "yf")})
               ELSE xi.id IS NOT NULL AND {_snoozed("xi")}
           END) AS still_held,
           max(held.kind) FILTER (WHERE held.kind IN ({_ENDING})) AS held_ending,
           ((xi.id IS NOT NULL AND xi.resolved_at IS NULL) OR xf.state = 'open') AS still_open,
           coalesce(xi.severity, xf.severity) AS severity,
           EXISTS (
               SELECT 1 FROM notifications AS news
               WHERE news.org_id = held.org_id AND news.channel_id = held.channel_id
                 AND news.incident_id IS NOT DISTINCT FROM held.incident_id
                 AND news.finding_id IS NOT DISTINCT FROM held.finding_id
                 AND news.kind IN ({_NEWS}) AND {_reached("news")}) AS told
    FROM notifications AS held
    LEFT JOIN incidents AS xi ON xi.id = held.incident_id AND xi.org_id = held.org_id
    LEFT JOIN findings AS xf ON xf.id = held.finding_id AND xf.org_id = held.org_id
    WHERE held.org_id = :org_id AND held.channel_id = :channel_id
      AND held.withheld_why IS NOT NULL AND held.released_at IS NULL
    GROUP BY held.org_id, held.channel_id, held.project_id, held.incident_id,
             held.finding_id, xi.id, xf.id
    """
)
# One row per subject and channel, `sequence` one above the highest of its kind there,
# so it never conflicts with what was withheld.
_WRITE_RELEASE: Final = text(
    """
    INSERT INTO notifications
        (org_id, project_id, incident_id, finding_id, channel_id, kind, sequence, severity,
         deliver_after, created_at)
    SELECT CAST(:org_id AS uuid), CAST(:project_id AS uuid), CAST(:incident_id AS uuid),
           CAST(:finding_id AS uuid), CAST(:channel_id AS uuid), CAST(:kind AS text),
           coalesce(max(sequence) + 1, 0), CAST(:severity AS text),
           CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND kind = CAST(:kind AS text)
      AND incident_id IS NOT DISTINCT FROM CAST(:incident_id AS uuid)
      AND finding_id IS NOT DISTINCT FROM CAST(:finding_id AS uuid)
    """
)
_STAMP_RELEASED: Final = text(
    """
    UPDATE notifications SET released_at = :now
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[]))
    """
)
# The channels the delivery sweep releases on: those holding rows not yet released,
# each locked as a claim locks it, so a sweep and a claim never release one twice.
_CHANNELS_HOLDING: Final = text(
    """
    SELECT id, org_id FROM notification_channels
    WHERE EXISTS (
            SELECT 1 FROM notifications
            WHERE notifications.org_id = notification_channels.org_id
              AND notifications.channel_id = notification_channels.id
              AND notifications.withheld_why IS NOT NULL
              AND notifications.released_at IS NULL)
      AND (muted_until IS NULL OR muted_until <= :now)
    ORDER BY id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)

_TAKEN: Final = text(
    f"""
    SELECT notifications.id, notifications.project_id, notifications.incident_id,
           notifications.finding_id, notifications.kind, notifications.severity,
           notifications.created_at, notifications.attempts,
           incidents.rule_id, incidents.rule_name, incidents.condition_kind,
           incidents.subject_key, incidents.subject_label, incidents.opened_at,
           incidents.resolved_at, incidents.detail,
           findings.scope, findings.service_name, findings.title, findings.signals,
           findings.started_at, findings.closed_at, findings.closed_reason,
           case_root.id AS case_root_id, case_root.case_opened_at AS case_opened_at,
           projects.name AS project_name,
           finding_rule.id AS finding_rule_id, finding_rule.name AS finding_rule_name,
           coalesce(notifications.dedup_key, {_OWN_KEY}) AS pagerduty_key,
           {_OWN_KEY} AS own_key,
           adopted.shifts AS adopted_shifts
    FROM notifications
    JOIN projects
        ON projects.id = notifications.project_id AND projects.org_id = notifications.org_id
    LEFT JOIN incidents
        ON incidents.id = notifications.incident_id AND incidents.org_id = notifications.org_id
    LEFT JOIN findings
        ON findings.id = notifications.finding_id AND findings.org_id = notifications.org_id
    -- The case the finding is part of now, read as it is sent (F22 FR47): a finding
    -- that left its case after this row was written names none.
    LEFT JOIN findings AS case_root
        ON case_root.id = findings.root_finding_id AND case_root.org_id = findings.org_id
    -- A finding's rows name no rule: one rule of its kind that sends to this channel
    -- names the message, while one exists.
    LEFT JOIN LATERAL (
        SELECT alert_rules.id, alert_rules.name
        FROM alert_rules
        JOIN alert_rule_channels
            ON alert_rule_channels.rule_id = alert_rules.id
           AND alert_rule_channels.org_id = alert_rules.org_id
        WHERE notifications.finding_id IS NOT NULL
          AND notifications.kind <> 'adopted'
          AND alert_rules.org_id = notifications.org_id
          AND alert_rules.project_id = notifications.project_id
          AND alert_rules.condition_kind = 'anomaly_finding'
          AND alert_rule_channels.channel_id = notifications.channel_id
        ORDER BY alert_rules.name, alert_rules.id
        LIMIT 1
    ) AS finding_rule ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object('signal', shifts.signal, 'ratio', shifts.ratio)
                         ORDER BY shifts.signal) AS shifts
        FROM baseline_shifts AS shifts
        WHERE notifications.kind = 'adopted'
          AND shifts.org_id = notifications.org_id
          AND shifts.project_id = notifications.project_id
          AND shifts.service_name = findings.service_name
          AND shifts.reason = 'adopted'
          AND shifts.from_minute >= findings.started_at
    ) AS adopted ON true
    WHERE notifications.org_id = :org_id AND notifications.id = ANY(CAST(:ids AS uuid[]))
    ORDER BY notifications.created_at
    """
)
_FIRST_ATTEMPT: Final = text(
    """
    SELECT min(attempted_at) FROM notification_attempts
    WHERE org_id = :org_id AND batch_id = :batch_id
    """
)
_INSERT_ATTEMPT: Final = text(
    """
    INSERT INTO notification_attempts
        (org_id, channel_id, batch_id, attempted_at, outcome, http_status, reason, kind)
    VALUES (:org_id, :channel_id, :batch_id, :now, :outcome, :http_status, :reason, :kind)
    """
)
_DELIVERED: Final = text(
    """
    UPDATE notifications
    SET state = 'delivered', delivered_at = :now, lease_until = NULL, attempts = attempts + 1
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_RETRY_LATER: Final = text(
    """
    UPDATE notifications
    SET state = 'pending', deliver_after = :retry_at, lease_until = NULL,
        attempts = attempts + 1, outcome_reason = :reason
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_FAILED: Final = text(
    """
    UPDATE notifications
    SET state = 'failed', lease_until = NULL, attempts = attempts + 1, outcome_reason = :reason
    WHERE org_id = :org_id AND id = ANY(CAST(:ids AS uuid[])) AND lease_until = :lease_until
    """
)
_CHANNEL_DELIVERED: Final = text(
    """
    UPDATE notification_channels
    SET last_delivered_at = :now, failing_since = NULL, failing_reported_at = NULL,
        health = 'healthy'
    WHERE org_id = :org_id AND id = :channel_id
    """
)
_CHANNEL_FAILED: Final = text(
    """
    UPDATE notification_channels SET failing_since = coalesce(failing_since, :now)
    WHERE org_id = :org_id AND id = :channel_id
    """
)
_PRUNE_ATTEMPTS: Final = text("DELETE FROM notification_attempts WHERE attempted_at < :before")


@dataclass(frozen=True, slots=True)
class Channel:
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    config: dict[str, Any]
    sealed_secret: bytes | None


SubjectKind = Literal["incident", "finding", "project"]
# A service finding's subject key is this and the service's name, as it was sent.
SERVICE_KEY_PREFIX: Final = "service:"

# A subject's name can come from telemetry, which anyone with a public browser key can
# send. Long enough for any real name; short enough that one cannot bury the message.
NAME_LIMIT: Final = 200
# C0 and C1 controls and the Unicode line and paragraph separators: anything that could
# start a line the message did not write.
_CONTROLS: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def one_line(name: str) -> str:
    """A name as one line of at most `NAME_LIMIT` characters, so it can only ever be
    words inside a message, never a line of its own."""
    flat = _CONTROLS.sub(" ", name).strip()
    return flat if len(flat) <= NAME_LIMIT else flat[: NAME_LIMIT - 1] + "…"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a notification is about, whichever kind it is (ADR-022). Its label is
    flattened here, once, so every message and every machine payload carries it as
    one short line (`docs/product/setup/alerts/webhooks.md`)."""

    kind: SubjectKind
    # The incident's, the finding's, or the project's own id.
    id: uuid.UUID
    # Identity for people and machines: the incident's subject key, the finding's
    # service, or the project.
    key: str
    # Words for people: a check, a service or a project. Telemetry text for a
    # service, so it is escaped by every channel that shows it.
    label: str
    opened_at: datetime
    closed_at: datetime | None
    rule_id: uuid.UUID | None
    rule_name: str | None
    condition_kind: str
    detail: dict[str, Any]
    # A finding's case, kept apart from `detail` so no machine payload can carry it.
    case: CaseOfSubject | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", one_line(self.label))


@dataclass(frozen=True, slots=True)
class CaseOfSubject:
    """The case a finding is part of when its message is composed (F22 FR47)."""

    root_id: uuid.UUID
    opened_at: datetime


@dataclass(frozen=True, slots=True)
class Notice:
    """One notification in a batch, with the subject it is about."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    severity: str
    created_at: datetime
    # Attempts already made, before the one in hand.
    attempts: int
    subject: Subject
    # The PagerDuty alert this notice goes to: its subject's own, or, for a
    # PagerDuty channel, a linked subject's that reached it first (D29, D35).
    pagerduty_key: str
    # It goes to a linked subject's alert, as a note there rather than a page.
    noted: bool = False


@dataclass(frozen=True, slots=True)
class Batch:
    """What one message to one channel carries. Its id is the webhook's `webhook-id`,
    the same on every retry."""

    id: uuid.UUID
    channel: Channel
    notices: tuple[Notice, ...]
    # The claim's own lease: an outcome is recorded against the rows only while
    # they still carry it, so a worker that outlived its lease cannot undo the
    # claim that replaced it.
    lease_until: datetime

    @property
    def ids(self) -> list[uuid.UUID]:
        return [notice.id for notice in self.notices]


@dataclass(frozen=True, slots=True)
class SendResult:
    """What a receiver did with one batch.

    `retry` is a failure worth trying again: the network, a timeout, 429 or 5xx.
    `failed` is final: any other refusal, whose reason is recorded (FR19).
    """

    outcome: Literal["delivered", "retry", "failed"]
    http_status: int | None = None
    # In words, for a person reading the delivery log. Never a destination's secret.
    reason: str | None = None
    retry_after_seconds: int | None = None


@dataclass(slots=True)
class Claim:
    batches: list[Batch] = field(default_factory=list)
    dropped: int = 0
    # Channels that had due work, whether it was sent, dropped by a mute or held:
    # zero is the only sign there is nothing left to do.
    channels: int = 0


async def claim(
    connection: AsyncConnection,
    *,
    now: datetime,
    limit: int,
    org_id: uuid.UUID | None = None,
) -> Claim:
    """Take the oldest due batch from up to `limit` channels, each under a lease —
    every organization's, or one's."""
    claimed = Claim()
    channels = await connection.execute(
        _CLAIMABLE_CHANNELS, {"now": now, "limit": limit, "org_id": org_id}
    )
    for row in channels.fetchall():
        claimed.channels += 1
        keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
        if row.muted_until is not None and row.muted_until > now:
            # Written as `dropped` rather than left or deleted, so the delivery log
            # still shows what the mute withheld (BR5).
            dropped = await connection.execute(_DROP_DUE, keys | {"reason": MUTED_CHANNEL_REASON})
            claimed.dropped += dropped.rowcount
            continue

        involved = False
        if row.kind in READ_BY_PEOPLE or row.kind == PAGERDUTY:
            # Before shaping, so a release goes in one message with Y's ending (AC52).
            await release(connection, org_id=row.org_id, channel_id=row.id, now=now)
            involved = bool((await connection.execute(_BURN_INVOLVED, keys)).scalar_one())
            if involved:
                claimed.dropped += await _drop_snoozed(connection, row, now=now)
        if row.kind in READ_BY_PEOPLE:
            await _shape(connection, row, now=now)
            if involved:
                claimed.dropped += await _withhold_second(connection, row, now=now)

        oldest = (await connection.execute(_OLDEST_DUE, keys)).first()
        if oldest is None:
            continue
        if oldest.batch_id is None and row.kind in READ_BY_PEOPLE:
            # Everything due for people goes as one message.
            everything = await connection.execute(_ALL_DUE, keys)
            batch_id, ids = uuid.uuid4(), [due.id for due in everything]
        elif oldest.batch_id is None:
            batch_id, ids = uuid.uuid4(), [oldest.id]
        else:
            # A retry: the same rows under the same id, so the receiver can tell it
            # has seen this delivery before (FR4).
            batch_id = oldest.batch_id
            in_batch = await connection.execute(_DUE_IN_BATCH, keys | {"batch_id": batch_id})
            ids = [taken.id for taken in in_batch]
        lease_until = now + LEASE
        await connection.execute(
            _TAKE,
            {"org_id": row.org_id, "ids": ids, "batch_id": batch_id, "lease_until": lease_until},
        )
        if row.kind == PAGERDUTY:
            withheld = await _one_alert_per_problem(connection, row.org_id, ids)
            claimed.dropped += len(withheld)
            ids = [taken for taken in ids if taken not in withheld]
            if not ids:
                continue
        notices = await connection.execute(_TAKEN, {"org_id": row.org_id, "ids": ids})
        claimed.batches.append(
            Batch(
                id=batch_id,
                channel=Channel(
                    id=row.id,
                    org_id=row.org_id,
                    kind=row.kind,
                    name=row.name,
                    config=row.config,
                    sealed_secret=row.sealed_secret,
                ),
                notices=tuple(_notice(notice) for notice in notices),
                lease_until=lease_until,
            )
        )
    return claimed


async def _drop_snoozed(connection: AsyncConnection, row: Any, *, now: datetime) -> int:
    """F19 FR26: hold a snoozed service's burn rows. Returns the rows dropped."""
    keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
    snoozed = await connection.execute(
        _DROP_SNOOZED,
        keys | {"reason": SNOOZED_REASON, "people": row.kind in READ_BY_PEOPLE},
    )
    return len(snoozed.fetchall())


async def _withhold_second(connection: AsyncConnection, row: Any, *, now: datetime) -> int:
    """FR22 and FR24 on a channel people read: a second message of the same news is
    recorded and not sent, and so is an ending of a subject this channel never heard
    of. Returns the rows dropped."""
    keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
    news = await connection.execute(
        _WITHHOLD_SECOND,
        keys
        | {
            "reason": SAME_NEWS_REASON,
            "told_since": now - SAME_NEWS_WINDOW,
            "fresh_since": now - BURN_FRESH_FOR,
        },
    )
    endings = await connection.execute(
        _WITHHOLD_UNTOLD_ENDING, keys | {"reason": NOTHING_TOLD_REASON}
    )
    return len(news.fetchall()) + len(endings.fetchall())


async def release(
    connection: AsyncConnection, *, org_id: uuid.UUID, channel_id: uuid.UUID, now: datetime
) -> int:
    """F19 FR23: for each subject held back on this channel and held back by nothing any
    more, one message after all, at its current severity: `opened` while it is still
    open and no news of it reached the channel, or a snoozed ending to a channel its
    news did reach. Every such row is then marked released, written or not, so it is
    released once. The caller holds the channel's lock. Returns the rows written."""
    keys = {"org_id": org_id, "channel_id": channel_id, "now": now}
    held = await connection.execute(_RELEASABLE, keys | {"fresh_since": now - BURN_FRESH_FOR})
    written = 0
    for subject in held.fetchall():
        if subject.still_held:
            continue
        owed = _owed_on_release(subject)
        if owed is not None:
            kind, severity = owed
            await connection.execute(
                _WRITE_RELEASE,
                keys
                | {
                    "project_id": subject.project_id,
                    "incident_id": subject.incident_id,
                    "finding_id": subject.finding_id,
                    "kind": kind,
                    "severity": severity,
                },
            )
            written += 1
        await connection.execute(_STAMP_RELEASED, keys | {"ids": subject.ids})
    return written


def _owed_on_release(subject: Any) -> tuple[str, str] | None:
    """What a channel is owed when nothing holds a subject back any more: its kind and
    severity, or None when it heard the news already or the subject ended unheard."""
    if subject.still_open:
        return None if subject.told else ("opened", subject.severity)
    if subject.held_ending is not None and subject.told:
        return subject.held_ending, subject.severity
    return None


async def release_everywhere(
    connection: AsyncConnection, *, now: datetime, limit: int = 100
) -> int:
    """The delivery sweep's release, across every channel holding rows back, for the
    endings that write nothing to a channel, such as a person closing a finding (AC52).
    Returns the rows written."""
    channels = await connection.execute(_CHANNELS_HOLDING, {"now": now, "limit": limit})
    written = 0
    for channel in channels.fetchall():
        written += await release(connection, org_id=channel.org_id, channel_id=channel.id, now=now)
    return written


async def _one_alert_per_problem(
    connection: AsyncConnection, org_id: uuid.UUID, ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Fix each taken row's PagerDuty key, and withhold an ending whose alert still
    carries another open subject (D35). Returns the withheld rows."""
    keys = {"org_id": org_id, "ids": ids}
    await connection.execute(_PIN_KEY, keys)
    withheld = await connection.execute(
        _WITHHOLD_ENDING, keys | {"reason": ALERT_STILL_OPEN_REASON}
    )
    return {row.id for row in withheld}


async def _shape(connection: AsyncConnection, row: Any, *, now: datetime) -> None:
    """Hold what should not go yet: warnings in quiet hours, anything past the burst cap."""
    keys = {"org_id": row.org_id, "channel_id": row.id, "now": now}
    try:
        quiet = quiet_until(parse_quiet_hours(row.quiet_hours), now)
    except ValidationError:
        quiet = None  # a stored shape the API would not have accepted; send as usual
    if quiet is not None:
        await connection.execute(_HOLD_WARNINGS, keys | {"until": quiet})

    sent, first_sent = (
        await connection.execute(_RECENT_MESSAGES, keys | {"since": now - BURST_WINDOW})
    ).one()
    if sent >= BURST_LIMIT and first_sent is not None:
        await connection.execute(_HOLD_ALL, keys | {"until": first_sent + BURST_WINDOW})


async def record(
    connection: AsyncConnection, batch: Batch, result: SendResult, *, now: datetime
) -> str:
    """Record one attempt and move the batch on. Returns the attempt's outcome."""
    keys = {"org_id": batch.channel.org_id, "ids": batch.ids, "lease_until": batch.lease_until}
    outcome = result.outcome
    reason = result.reason
    # From the first attempt, not the decision: a warning held overnight by quiet
    # hours still gets its hour of retries in the morning.
    first_attempt = (
        await connection.execute(
            _FIRST_ATTEMPT, {"org_id": batch.channel.org_id, "batch_id": batch.id}
        )
    ).scalar_one() or now
    if outcome == "retry" and now - first_attempt >= RETRY_WINDOW:
        outcome, reason = "failed", f"gave up after an hour of retrying: {reason}"

    await connection.execute(
        _INSERT_ATTEMPT,
        {
            "org_id": batch.channel.org_id,
            "channel_id": batch.channel.id,
            "batch_id": batch.id,
            "now": now,
            "outcome": "retrying" if outcome == "retry" else outcome,
            "http_status": result.http_status,
            "reason": reason,
            "kind": "notification",
        },
    )
    channel_keys = {"org_id": batch.channel.org_id, "channel_id": batch.channel.id, "now": now}
    if outcome == "delivered":
        await connection.execute(_DELIVERED, keys | {"now": now})
        await connection.execute(_CHANNEL_DELIVERED, channel_keys)
        return "delivered"

    await connection.execute(_CHANNEL_FAILED, channel_keys)
    if outcome == "retry":
        # This attempt is recorded above, so it counts: the first retry waits 30 s.
        attempts = 1 + max(notice.attempts for notice in batch.notices)
        wait = retry_delay(attempts, result)
        await connection.execute(
            _RETRY_LATER,
            keys | {"retry_at": now + timedelta(seconds=wait), "reason": reason},
        )
        return "retrying"
    await connection.execute(_FAILED, keys | {"reason": reason})
    return "failed"


def retry_delay(attempts: int, result: SendResult) -> float:
    """Exponential from 30 seconds, capped at ten minutes, with jitter so a
    receiver that came back is not met by every channel's retry in one second."""
    base = min(FIRST_RETRY_SECONDS * 2 ** max(attempts - 1, 0), LONGEST_RETRY_SECONDS)
    # Spreading load, not a secret: `random` is the right tool.
    delay: float = base * random.uniform(0.8, 1.2)
    if result.retry_after_seconds is not None:
        delay = max(delay, float(min(result.retry_after_seconds, LONGEST_RETRY_AFTER_SECONDS)))
    return delay


async def prune_attempts(connection: AsyncConnection, *, before: datetime) -> int:
    """The delivery log is kept 30 days (FR20)."""
    return (await connection.execute(_PRUNE_ATTEMPTS, {"before": before})).rowcount


def _notice(row: Any) -> Notice:
    return Notice(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        severity=row.severity,
        created_at=row.created_at,
        attempts=row.attempts,
        subject=_subject(row),
        pagerduty_key=row.pagerduty_key,
        noted=row.pagerduty_key != row.own_key,
    )


def _subject(row: Any) -> Subject:
    if row.incident_id is not None:
        return Subject(
            kind="incident",
            id=row.incident_id,
            key=row.subject_key,
            label=row.subject_label,
            opened_at=row.opened_at,
            closed_at=row.resolved_at,
            rule_id=row.rule_id,
            rule_name=row.rule_name,
            condition_kind=row.condition_kind,
            detail=row.detail,
        )
    if row.finding_id is not None:
        return Subject(
            kind="finding",
            id=row.finding_id,
            key=f"{SERVICE_KEY_PREFIX}{row.service_name}"
            if row.service_name is not None
            else "project",
            label=row.service_name if row.service_name is not None else row.project_name,
            opened_at=row.started_at,
            closed_at=row.closed_at,
            rule_id=row.finding_rule_id,
            rule_name=row.finding_rule_name,
            condition_kind=ANOMALY_FINDING,
            detail={
                "scope": row.scope,
                "title": row.title,
                "signals": row.signals,
                "closed_reason": row.closed_reason,
                "project": row.project_name,
                "adopted_shifts": row.adopted_shifts or [],
            },
            case=None
            if row.case_root_id is None or row.case_opened_at is None
            else CaseOfSubject(root_id=row.case_root_id, opened_at=row.case_opened_at),
        )
    # Detection stopping and running again: the project is the subject (D24).
    return Subject(
        kind="project",
        id=row.project_id,
        key="project",
        label=row.project_name,
        opened_at=row.created_at,
        closed_at=None,
        rule_id=None,
        rule_name=None,
        condition_kind=DETECTION,
        detail={},
    )
