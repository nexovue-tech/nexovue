"""What `cases_sweep` sends (F22 plan T4.6; design, *Keeping and ending*, step 5): the
case emails owed through `finding_emails_owed`, and each connection's failure and expiry
notices (FR33).

It runs on `scheduler-light`, which holds the mail settings; `scheduler-connections`
holds none (D30), so the sync owes an email by writing a row and this sends it.

**At most once, never twice** (D90, AC94). An owed row is deleted, and a connection's
marker (`failure_told_at`, `expiry_told_at`) set, in a transaction that commits before
any send. A crash or a refusing provider loses that email; nothing repeats it. A row per
email means a second one owed before this runs cannot overwrite the first.

**No outside words** (D28): an email names the service in its strict form, a fixed
sentence, a date and a link. Never a finding title, a note, a tracker's text, or a
provider's error. Each first read crosses organizations by nature and is a named
exception in the tenancy test; every follow-on statement binds the row's `org_id`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from nexovue_platform.cases import CASE_IDLE_LIMIT
from nexovue_platform.connections import TRACKER_KINDS, strict_name
from nexovue_platform.email import EmailError, Message
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nexovue_scheduler.alerts.senders.email import verified_admins

logger = get_logger(__name__)

# How many owed emails, and connections, one sweep takes; the next takes the rest.
BATCH: Final = 200
# FR33: a connection `failing` this long tells its admins, a guess.
FAILING_TELLS_AFTER: Final = timedelta(hours=24)
# FR33, D37: a credential's stated expiry is told this far ahead.
EXPIRY_TOLD_AHEAD: Final = timedelta(days=14)

_CASE_MESSAGES: Final = {
    "tracker_closed": Message.CASE_TRACKER_CLOSED,
    "tracker_closed_signal_open": Message.CASE_TRACKER_CLOSED_SIGNAL_OPEN,
    "idle_warning": Message.CASE_IDLE_WARNING,
}
_TRACKER_NAMES: Final = {"github": "GitHub", "jira": "Jira"}
# What broke, in words, by `CONNECTION_FAILURES`' enum; never the provider's text.
_FAILURE_WORDS: Final = {
    "unauthorized": "the tracker refuses the stored key, or the App was uninstalled.",
    "app_not_installed": "the App is not installed on any account.",
    "installation_ambiguous": "the App is installed in more than one place.",
    "no_repository": "the App reaches no repository.",
    "key_unreadable": "this install's connection key cannot open the stored credential.",
    "unreachable": "the tracker could not be reached.",
    "timed_out": "the tracker did not answer in time.",
    "server_error": "the tracker answered with an error.",
    "rate_limited": "the tracker asked nexovue to slow down.",
    "unexpected_answer": "the tracker answered in a way nexovue did not expect.",
}
_SOMETHING_FAILED: Final = "a call to the tracker failed."


class Sender(Protocol):
    async def send(
        self, *, to: str, message: Message, data: dict[str, Any], idempotency_key: str | None = None
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class _Email:
    message: Message
    recipients: Sequence[str]
    data: dict[str, Any]
    key: str


# ------------------------------------------------------------ case emails

_OWED: Final = text(
    """
    SELECT org_id, id FROM finding_emails_owed
    ORDER BY at, id
    LIMIT :batch
    """
)
_TAKE_OWED: Final = text(
    """
    DELETE FROM finding_emails_owed
    WHERE org_id = :org_id AND id = :id
    RETURNING finding_id, kind
    """
)
_CASE_OF: Final = text(
    """
    SELECT finding.id, finding.service_name, finding.case_activity_at, person.email
    FROM findings AS finding
    LEFT JOIN users AS person
      ON person.org_id = finding.org_id AND person.id = finding.assignee_id
     AND person.removed_at IS NULL
    WHERE finding.org_id = :org_id AND finding.id = :finding_id
    """
)


async def send_owed_case_emails(
    engine: AsyncEngine, *, sender: Sender | None, app_base_url: str
) -> int:
    """Each owed row goes to the case's assignee, or to owners and admins if nobody is
    assigned (AC94). With no mail configured, rows wait: nothing is lost by waiting."""
    if sender is None:
        return 0
    async with engine.connect() as connection:
        owed = list(await connection.execute(_OWED, {"batch": BATCH}))
    sent = 0
    for row in owed:
        async with engine.begin() as connection:
            email = await _case_email(connection, row.org_id, row.id, app_base_url=app_base_url)
        if email is not None:
            sent += await _send(sender, email)
    return sent


async def _case_email(
    connection: AsyncConnection, org_id: Any, owed_id: Any, *, app_base_url: str
) -> _Email | None:
    taken = (await connection.execute(_TAKE_OWED, {"org_id": org_id, "id": owed_id})).one_or_none()
    if taken is None:
        return None
    case = (
        await connection.execute(_CASE_OF, {"org_id": org_id, "finding_id": taken.finding_id})
    ).one_or_none()
    if case is None:
        return None
    recipients = [case.email] if case.email else await verified_admins(connection, org_id)
    data: dict[str, Any] = {
        "service": strict_name(case.service_name),
        "link": f"{app_base_url.rstrip('/')}/findings/{case.id}",
    }
    if taken.kind == "idle_warning" and case.case_activity_at is not None:
        data["ends_on"] = _day(case.case_activity_at + CASE_IDLE_LIMIT)
    return _Email(
        message=_CASE_MESSAGES[taken.kind],
        recipients=recipients,
        data=data,
        key=f"case-{taken.kind}-{owed_id}",
    )


# ------------------------------------------------------ connection emails

_TO_TELL: Final = text(
    """
    SELECT org_id, id FROM connections
    WHERE kind = ANY(CAST(:kinds AS text[])) AND state <> 'disconnected'
      AND ((failure_told_at IS NULL
            AND (state = 'needs_attention'
                 OR (state = 'failing' AND failing_since <= :failing_before)))
        OR (expiry_told_at IS NULL AND token_expires_on IS NOT NULL
            AND token_expires_on <= :expires_by))
    ORDER BY id
    LIMIT :batch
    """
)
_LOCK_CONNECTION: Final = text(
    """
    SELECT id, kind, state, last_failure, failing_since, failure_told_at, token_expires_on,
           expiry_told_at
    FROM connections
    WHERE org_id = :org_id AND id = :id AND state <> 'disconnected'
    FOR NO KEY UPDATE SKIP LOCKED
    """
)
_TOLD: Final = text(
    """
    UPDATE connections
    SET failure_told_at = COALESCE(:failure_told_at, failure_told_at),
        expiry_told_at = COALESCE(:expiry_told_at, expiry_told_at)
    WHERE org_id = :org_id AND id = :id
    """
)


async def tell_connection_admins(
    engine: AsyncEngine, *, sender: Sender | None, app_base_url: str, now: datetime
) -> int:
    """FR33, AC78: one email when a connection needs attention or has failed for a day,
    and one before a stated expiry, each with its own marker. The failure marker clears
    when the connection works again (`connections/store.py`), ending the episode."""
    if sender is None:
        return 0
    async with engine.connect() as connection:
        due = list(
            await connection.execute(
                _TO_TELL,
                {
                    "kinds": list(TRACKER_KINDS),
                    "failing_before": now - FAILING_TELLS_AFTER,
                    "expires_by": (now + EXPIRY_TOLD_AHEAD).date(),
                    "batch": BATCH,
                },
            )
        )
    sent = 0
    for row in due:
        async with engine.begin() as connection:
            emails = await _connection_emails(
                connection, row.org_id, row.id, app_base_url=app_base_url, now=now
            )
        for email in emails:
            sent += await _send(sender, email)
    return sent


async def _connection_emails(
    connection: AsyncConnection,
    org_id: Any,
    connection_id: Any,
    *,
    app_base_url: str,
    now: datetime,
) -> list[_Email]:
    row = (
        await connection.execute(_LOCK_CONNECTION, {"org_id": org_id, "id": connection_id})
    ).one_or_none()
    if row is None:
        return []
    tracker = _TRACKER_NAMES.get(row.kind, row.kind)
    link = f"{app_base_url.rstrip('/')}/settings/connections"
    what = _FAILURE_WORDS.get(row.last_failure or "", _SOMETHING_FAILED)
    emails: list[tuple[Message, dict[str, Any], str]] = []
    failure_told = expiry_told = None
    if row.failure_told_at is None and row.state == "needs_attention":
        emails.append(
            (
                Message.CONNECTION_NEEDS_ATTENTION,
                {"tracker": tracker, "what": what, "link": link},
                "attention",
            )
        )
        failure_told = now
    elif (
        row.failure_told_at is None
        and row.state == "failing"
        and row.failing_since is not None
        and row.failing_since <= now - FAILING_TELLS_AFTER
    ):
        emails.append(
            (
                Message.CONNECTION_FAILING,
                {
                    "tracker": tracker,
                    "what": what,
                    "link": link,
                    "failing_since": f"{row.failing_since:%Y-%m-%d %H:%M} UTC",
                },
                "failing",
            )
        )
        failure_told = now
    if (
        row.expiry_told_at is None
        and row.token_expires_on is not None
        and row.token_expires_on <= (now + EXPIRY_TOLD_AHEAD).date()
    ):
        emails.append(
            (
                Message.CONNECTION_EXPIRING,
                {"tracker": tracker, "expires_on": _day(row.token_expires_on), "link": link},
                "expiry",
            )
        )
        expiry_told = now
    if not emails:
        return []
    await connection.execute(
        _TOLD,
        {
            "org_id": org_id,
            "id": connection_id,
            "failure_told_at": failure_told,
            "expiry_told_at": expiry_told,
        },
    )
    recipients = await verified_admins(connection, org_id)
    return [
        _Email(
            message=message,
            recipients=recipients,
            data=data,
            key=f"connection-{connection_id}-{why}-{now:%Y%m%d%H%M}",
        )
        for message, data, why in emails
    ]


# ------------------------------------------------------------------ sending


async def _send(sender: Sender, email: _Email) -> int:
    """Each recipient once; a refusal is logged and not retried (D90)."""
    if not email.recipients:
        logger.error("an owed email has nobody to go to", message=email.message.value)
        return 0
    sent = 0
    for recipient in email.recipients:
        try:
            await sender.send(
                to=recipient,
                message=email.message,
                data=email.data,
                idempotency_key=_key(email.key, recipient),
            )
            sent += 1
        except EmailError as refused:
            logger.error(
                "an owed email was refused and is not retried",
                message=email.message.value,
                reason=str(refused),
            )
    return sent


def _key(key: str, recipient: str) -> str:
    return f"f22-{hashlib.sha256(f'{key}/{recipient}'.encode()).hexdigest()}"


def _day(moment: datetime | date) -> str:
    return f"{moment.day} {moment:%b %Y}"
