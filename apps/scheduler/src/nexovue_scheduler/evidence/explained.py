"""F3's follow-up message, `explained` (spec FR21, FR22, FR48; design, *Notification:
`explained`*; plan T2.7).

One message per finding per channel, written from facts by template and never from
model words, when a `likely` cause with basis `verified` stands on a read after the
settled time. It is written by the gather job, in the gather's own transaction, so a
crash leaves neither the version nor the row.

**Where the limit is enforced.** Here, where the row is written, never at delivery: a
kind outside `ENDING_KINDS` reaching PagerDuty would be a `trigger`, and a webhook a new
event type (research, *traps*). So only channels people read get one, only those already
told about the finding, only those that have not turned it off, and only those holding
no `explained` row for it **at any sequence**, because the notifications' unique key
includes the sequence (`notify._INSERT_FOR_FINDING`) and alone would allow a second.

**The cap.** The outbox holds a channel to 10 messages a minute, which over minutes is
still far too many follow-ups. So a channel gets at most `PER_CHANNEL` in
`CAP_WINDOW`; the last of them is written with `LAST_SEQUENCE`, whose words say more
findings have a likely place to look. Gathers run two at once, and a lock on one finding
does not cover a channel's count, so each channel is counted under a transaction-level
advisory lock of its own, taken in sorted order so two gathers cannot deadlock.

Every statement binds `org_id` (`test_scheduler_postgres_tenancy_structure.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from nexovue_platform.alerts.channels import READ_BY_PEOPLE
from nexovue_platform.alerts.model import EXPLAINED
from nexovue_platform.detection import FINDING_TOLD_KINDS
from nexovue_platform.evidence import (
    EXPLAINED_FIRST_SEQUENCE,
    EXPLAINED_LAST_SEQUENCE,
    LIKELY,
    OPENED,
    SETTLE_DELAY_SECONDS,
    VERIFIED,
    cause_released,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# At most this many follow-ups to one channel in this long (FR21; guesses).
PER_CHANNEL: Final = 5
CAP_WINDOW: Final = timedelta(minutes=10)
# The sequence of the last follow-up a channel gets in a window: its words say more
# findings have a likely place to look on the feed (design, *The cap*).
FIRST_SEQUENCE: Final = EXPLAINED_FIRST_SEQUENCE
LAST_SEQUENCE: Final = EXPLAINED_LAST_SEQUENCE
# "After the settled time": the settled read's own delay (design, gather step 8).
SETTLED_AFTER: Final = timedelta(seconds=SETTLE_DELAY_SECONDS)

_FINDING: Final = text(
    """
    SELECT findings.state, findings.muted, findings.flapping, findings.severity,
           findings.project_id,
           coalesce(settings.muted_until > :now, false)
               OR coalesce(settings.snoozed_until > :now, false) AS quiet
    FROM findings
    LEFT JOIN service_detection_settings AS settings
        ON settings.org_id = findings.org_id AND settings.project_id = findings.project_id
       AND settings.service_name = findings.service_name
    WHERE findings.org_id = :org_id AND findings.id = :finding_id
    """
)
# Channels told this finding's news, that people read, that send follow-ups, and that
# hold no follow-up for it at any sequence.
_CHANNELS: Final = text(
    """
    SELECT DISTINCT channel.id
    FROM notifications AS told
    JOIN notification_channels AS channel
        ON channel.org_id = told.org_id AND channel.id = told.channel_id
    WHERE told.org_id = :org_id AND told.finding_id = :finding_id
      AND told.kind = ANY(CAST(:told_kinds AS text[]))
      AND channel.kind = ANY(CAST(:people AS text[]))
      AND channel.sends_explained
      AND NOT EXISTS (
          SELECT 1 FROM notifications AS already
          WHERE already.org_id = told.org_id AND already.finding_id = told.finding_id
            AND already.channel_id = channel.id AND already.kind = :kind)
    ORDER BY channel.id
    """
)
_LOCK_CHANNEL: Final = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_RECENT: Final = text(
    """
    SELECT count(*) FROM notifications
    WHERE org_id = :org_id AND channel_id = :channel_id AND kind = :kind
      AND created_at > :since
    """
)
# The check is repeated inside the insert, under the channel's lock, so a gather that
# waited on the lock never writes a second row.
_INSERT: Final = text(
    """
    INSERT INTO notifications
        (org_id, project_id, finding_id, channel_id, kind, sequence, severity,
         deliver_after, created_at)
    SELECT CAST(:org_id AS uuid), CAST(:project_id AS uuid), CAST(:finding_id AS uuid),
           CAST(:channel_id AS uuid), CAST(:kind AS text), CAST(:sequence AS integer),
           CAST(:severity AS text), CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    WHERE NOT EXISTS (
        SELECT 1 FROM notifications AS already
        WHERE already.org_id = CAST(:org_id AS uuid)
          AND already.finding_id = CAST(:finding_id AS uuid)
          AND already.channel_id = CAST(:channel_id AS uuid)
          AND already.kind = CAST(:kind AS text))
    ON CONFLICT DO NOTHING
    RETURNING id
    """
)


def after_settling(reason: str, started_at: datetime, now: datetime) -> bool:
    """A read after the settled time: not the opening's, and the finding at least the
    settled delay old. Judged from the job, since a stored version does not say which
    read made it (design, gather step 8)."""
    return reason != OPENED and now - started_at >= SETTLED_AFTER


def sends_for(cause: Mapping[str, Any] | None, *, show_held_causes: bool) -> bool:
    """Only a `likely` cause resting on a verified record, and only once its rule is
    released (FR21, FR50)."""
    if cause is None:
        return False
    return (
        cause.get("strength") == LIKELY
        and cause.get("basis") == VERIFIED
        and cause_released(cause.get("rule"), override=show_held_causes)
    )


async def write(
    connection: AsyncConnection,
    *,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    now: datetime,
) -> int:
    """Write the follow-up for each channel it is owed to, in the caller's transaction.
    The caller has decided the cause qualifies and the read is after settling. Returns
    the rows written."""
    finding = (
        await connection.execute(_FINDING, {"org_id": org_id, "finding_id": finding_id, "now": now})
    ).first()
    if finding is None or finding.state != "open":
        return 0
    if finding.muted or finding.flapping or finding.quiet:
        return 0
    channels = await connection.execute(
        _CHANNELS,
        {
            "org_id": org_id,
            "finding_id": finding_id,
            "told_kinds": list(FINDING_TOLD_KINDS),
            "people": sorted(READ_BY_PEOPLE),
            "kind": EXPLAINED,
        },
    )
    written = 0
    for channel_id in sorted(row.id for row in channels):
        sequence = await _place_in_window(connection, org_id, channel_id, now)
        if sequence is None:
            continue
        inserted = await connection.execute(
            _INSERT,
            {
                "org_id": org_id,
                "project_id": finding.project_id,
                "finding_id": finding_id,
                "channel_id": channel_id,
                "kind": EXPLAINED,
                "sequence": sequence,
                "severity": finding.severity,
                "now": now,
            },
        )
        written += len(inserted.fetchall())
    return written


async def _place_in_window(
    connection: AsyncConnection, org_id: uuid.UUID, channel_id: uuid.UUID, now: datetime
) -> int | None:
    """Lock the channel's count for this transaction, then the sequence to write: the
    first sequence while the window has room, the last one for its final place, None
    when it is full."""
    await connection.execute(_LOCK_CHANNEL, {"key": f"{EXPLAINED}:{channel_id}"})
    recent = (
        await connection.execute(
            _RECENT,
            {
                "org_id": org_id,
                "channel_id": channel_id,
                "kind": EXPLAINED,
                "since": now - CAP_WINDOW,
            },
        )
    ).scalar_one()
    if recent >= PER_CHANNEL:
        return None
    return LAST_SEQUENCE if recent == PER_CHANNEL - 1 else FIRST_SEQUENCE


__all__ = ["CAP_WINDOW", "PER_CHANNEL", "after_settling", "sends_for", "write"]
