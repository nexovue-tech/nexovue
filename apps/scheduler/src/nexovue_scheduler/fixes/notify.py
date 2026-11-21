"""`fixes.notify_requesters`: the one email per event a requester asked to hear (F5 design,
*User experience*, *Jobs*; spec D46, AC64; plan T4.8), and each owner's one email when an
agreement to send code ended by itself (spec A7, AC59; design, *The sweep*, step 7; T5.3).

On `light`, because `scheduler-connections` holds no mail settings (AC4); so this is the
one F5 module in `TASK_MODULES`, and it imports nothing from `connections`. The pack job
and the sweep mark a row `email_due`; this sends and clears it.

**At most once, never twice:** a row's `email_due` is cleared, in a transaction that
commits before the send. A crash or a refusing provider loses that email; nothing repeats
it. **No outside words:** a fixed template and a finding link. Never a path, a file, the
model's words or a provider's error. The first read crosses organizations by nature and
is a named exception in the tenancy test; every follow-on statement binds the row's
`org_id`.

**An agreement that ended by itself** is found by comparing each unwithdrawn consent with
its organization's AI connection now (FR8): a changed address, format, key or model. Each
is marked `ended_notice_sent_at` once, in the transaction that reads the owners, and every
owner gets one email per pass naming what changed, in fixed words. Stored suggestions stay
(D43); the email says how to delete them. A person switching AI off, or removing the
connection, deletes code instead (BR9), so it owes no such email.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

from nexovue_platform.email import EmailError, Message, build_email_sender
from nexovue_platform.fixes.records import GATHERED, NOTIFY_REQUESTERS_TASK, PROPOSED
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

BATCH: Final = 200
_MESSAGE_OF: Final = {
    GATHERED: Message.FIX_FILES_READY,
    PROPOSED: Message.FIX_SUGGESTION_READY,
}

# Across every organization: rows whose requester is owed an email.
_DUE: Final = text(
    """
    SELECT org_id, id FROM finding_fixes
    WHERE email_due AND kind = 'source'
      AND (CAST(:only_org AS uuid) IS NULL OR org_id = CAST(:only_org AS uuid))
    ORDER BY state_changed_at
    LIMIT :batch
    """
)
# Taken before the send, so a crash after this loses the email rather than repeating it.
_TAKE: Final = text(
    """
    UPDATE finding_fixes AS fix SET email_due = false
    FROM users AS person
    WHERE fix.org_id = :org_id AND fix.id = :fix_id AND fix.email_due
      AND person.org_id = fix.org_id AND person.id = fix.requested_by
    RETURNING fix.finding_id, fix.state, fix.notify_requester, person.email,
              person.email_verified_at, person.removed_at
    """
)
_DROP: Final = text(
    "UPDATE finding_fixes SET email_due = false WHERE org_id = :org_id AND id = :fix_id"
)
# Across every organization: unwithdrawn agreements whose provider terms no longer equal
# the AI connection's, not yet told (FR8's comparison, as the pack job makes it).
_ENDED_CONSENTS: Final = text(
    """
    SELECT DISTINCT consent.org_id FROM fix_source_consents AS consent
    JOIN ai_connections AS ai ON ai.org_id = consent.org_id
    WHERE consent.withdrawn_at IS NULL AND consent.ended_notice_sent_at IS NULL
      AND (consent.provider_base_url IS DISTINCT FROM ai.base_url
           OR consent.provider_format IS DISTINCT FROM ai.wire_format
           OR consent.key_id IS DISTINCT FROM ai.key_id
           OR consent.model IS DISTINCT FROM ai.model)
      AND (CAST(:only_org AS uuid) IS NULL OR consent.org_id = CAST(:only_org AS uuid))
    ORDER BY consent.org_id
    LIMIT :batch
    """
)
# Taken before the send, as a requester's email is: a crash loses it, never repeats it.
_TAKE_ENDED: Final = text(
    """
    UPDATE fix_source_consents AS consent SET ended_notice_sent_at = now()
    FROM ai_connections AS ai
    WHERE consent.org_id = :org_id AND ai.org_id = consent.org_id
      AND consent.withdrawn_at IS NULL AND consent.ended_notice_sent_at IS NULL
      AND (consent.provider_base_url IS DISTINCT FROM ai.base_url
           OR consent.provider_format IS DISTINCT FROM ai.wire_format
           OR consent.key_id IS DISTINCT FROM ai.key_id
           OR consent.model IS DISTINCT FROM ai.model)
    RETURNING consent.provider_base_url IS DISTINCT FROM ai.base_url AS address,
              consent.provider_format IS DISTINCT FROM ai.wire_format AS format,
              consent.key_id IS DISTINCT FROM ai.key_id AS key,
              consent.model IS DISTINCT FROM ai.model AS model
    """
)
_OWNERS: Final = text(
    """
    SELECT email FROM users
    WHERE org_id = :org_id AND role = 'owner' AND removed_at IS NULL
      AND email_verified_at IS NOT NULL
    ORDER BY email
    """
)
# What changed, in the email's fixed words, in this order.
CHANGE_WORDS: Final = (
    ("address", "the AI provider's address"),
    ("format", "the AI provider's format"),
    ("key", "the AI provider's key"),
    ("model", "the AI model"),
)


class Sender(Protocol):
    async def send(
        self, *, to: str, message: Message, data: dict[str, Any], idempotency_key: str | None = None
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class Notified:
    sent: int
    dropped: int
    owners_told: int = 0


async def notify(
    engine: AsyncEngine, *, sender: Sender | None, app_base_url: str, only_org: str | None = None
) -> Notified:
    """Each due row, taken then sent. With no mail configured, rows wait: nothing is lost
    by waiting, as F22's case emails do. `only_org` narrows the pass to one organization,
    for a test, so a test's run never takes another test's email."""
    if sender is None:
        return Notified(sent=0, dropped=0)
    async with engine.connect() as connection:
        due = list(await connection.execute(_DUE, {"batch": BATCH, "only_org": only_org}))
    sent = dropped = 0
    for row in due:
        ids = {"org_id": row.org_id, "fix_id": row.id}
        async with engine.begin() as connection:
            taken = (await connection.execute(_TAKE, ids)).one_or_none()
            if taken is None:
                # A requester since removed from the organization: nothing is owed.
                await connection.execute(_DROP, ids)
        if taken is None or not _reachable(taken):
            dropped += 1
            continue
        message = _MESSAGE_OF.get(taken.state, Message.FIX_SUGGESTION_ENDED)
        link = f"{app_base_url.rstrip('/')}/findings/{taken.finding_id}"
        try:
            await sender.send(
                to=taken.email,
                message=message,
                data={"link": link},
                idempotency_key=f"fix-{row.id}-{taken.state}",
            )
            sent += 1
        except EmailError:
            logger.warning("a suggested fix email was not sent", fix_id=str(row.id))
    owners_told = await _tell_owners(
        engine, sender=sender, app_base_url=app_base_url, only_org=only_org
    )
    return Notified(sent=sent, dropped=dropped, owners_told=owners_told)


async def _tell_owners(
    engine: AsyncEngine, *, sender: Sender, app_base_url: str, only_org: str | None
) -> int:
    """AC59: each owner, once, for agreements that ended by themselves since the last pass."""
    async with engine.connect() as connection:
        due = list(
            await connection.execute(_ENDED_CONSENTS, {"batch": BATCH, "only_org": only_org})
        )
    told = 0
    link = f"{app_base_url.rstrip('/')}/settings/ai"
    for organization in due:
        ids = {"org_id": organization.org_id}
        async with engine.begin() as connection:
            ended = list(await connection.execute(_TAKE_ENDED, ids))
            owners = [row.email for row in await connection.execute(_OWNERS, ids)]
        changed = changed_words(ended)
        for owner in owners if ended else []:
            try:
                await sender.send(
                    to=owner,
                    message=Message.FIX_CONSENT_ENDED,
                    data={"changed": changed, "link": link},
                )
                told += 1
            except EmailError:
                logger.warning(
                    "a consent-ended email was not sent", org_id=str(organization.org_id)
                )
    return told


def changed_words(ended: list[Any]) -> str:
    """ "the AI provider's key and the AI model": each change once, in a fixed order."""
    words = [word for field, word in CHANGE_WORDS if any(getattr(row, field) for row in ended)]
    if len(words) <= 1:
        return words[0] if words else "the AI connection"
    return ", ".join(words[:-1]) + " and " + words[-1]


def _reachable(taken: Any) -> bool:
    return (
        bool(taken.notify_requester)
        and taken.email_verified_at is not None
        and taken.removed_at is None
    )


_sender: Sender | None = None
_sender_built = False


def _email_sender() -> Sender | None:
    global _sender, _sender_built
    if not _sender_built:
        configured = get_settings().email
        _sender = build_email_sender(configured) if configured else None
        _sender_built = True
    return _sender


@task(NOTIFY_REQUESTERS_TASK)
def notify_requesters_task() -> None:
    notified = with_engine(
        lambda engine: notify(
            engine, sender=_email_sender(), app_base_url=get_settings().app_base_url
        )
    )
    if notified.sent or notified.dropped or notified.owners_told:
        logger.info(
            "suggested fix emails",
            sent=notified.sent,
            dropped=notified.dropped,
            owners_told=notified.owners_told,
        )
