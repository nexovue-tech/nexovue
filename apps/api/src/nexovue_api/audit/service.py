"""Writing the audit log.

Two rules shape every function here.

**An entry joins the caller's transaction; it never opens one of its own.** The
row is added to the same `AsyncSession` the action is being performed on, so the
action and its record commit together or neither does. The failure this chooses
is an action that fails because its record could not be written — the opposite
choice loses history that cannot be reconstructed afterwards.

**Nothing recorded is worth stealing.** A target names a row by its primary key.
`api_keys.key_prefix` is deliberately not used for that: it carries seven
characters of the key's secret. A leak of this table discloses who acted and
when, and nothing that grants access to anything.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    ApiKey,
    AuditLogEntry,
    Check,
    Organization,
    User,
)

# `<entity>.<past tense>`, so a prefix match selects every event about one kind
# of thing. These strings are stored, so changing one is a migration.
ORGANIZATION_CREATED: Final = "organization.created"
SESSION_CREATED: Final = "session.created"
SESSION_ENDED: Final = "session.ended"
CHECK_CREATED: Final = "check.created"
CHECK_UPDATED: Final = "check.updated"
CHECK_DELETED: Final = "check.deleted"
API_KEY_CREATED: Final = "api_key.created"
API_KEY_REVOKED: Final = "api_key.revoked"
SESSION_DENIED: Final = "session.denied"

ORGANIZATION_TARGET: Final = "organization"
USER_TARGET: Final = "user"
API_KEY_TARGET: Final = "api_key"
CHECK_TARGET: Final = "check"

# A refused sign-in against an address matching no account. The address is not
# recorded: it belongs to somebody who is not our customer, so storing it would
# collect personal data on non-users and build a list of addresses somebody
# once guessed. The row is a count, and counts are what reveal brute force.
UNKNOWN_ACCOUNT_TARGET: Final = "unknown_account"


def record_organization_created(
    db: AsyncSession, *, organization: Organization, owner: User
) -> None:
    """Who signed up, and for which tenant."""
    _add(
        db,
        org_id=organization.id,
        actor_user_id=owner.id,
        action=ORGANIZATION_CREATED,
        target=_target(ORGANIZATION_TARGET, organization.id),
    )


def record_session_created(db: AsyncSession, *, user: User) -> None:
    """A session now exists for this user. Both signing in and signing up land here."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=SESSION_CREATED,
        target=_target(USER_TARGET, user.id),
    )


def record_session_ended(db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """A session was given up. Taken from the session itself, not from a request."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        action=SESSION_ENDED,
        target=_target(USER_TARGET, user_id),
    )


def record_api_key_created(
    db: AsyncSession, *, api_key: ApiKey, actor_user_id: uuid.UUID | None
) -> None:
    """An ingest credential was minted. `actor_user_id` is null when the system minted it."""
    _add(
        db,
        org_id=api_key.org_id,
        actor_user_id=actor_user_id,
        action=API_KEY_CREATED,
        target=_target(API_KEY_TARGET, api_key.id),
    )


def record_api_key_revoked(db: AsyncSession, *, api_key: ApiKey, actor_user_id: uuid.UUID) -> None:
    """An ingest credential was withdrawn, and by whom."""
    _add(
        db,
        org_id=api_key.org_id,
        actor_user_id=actor_user_id,
        action=API_KEY_REVOKED,
        target=_target(API_KEY_TARGET, api_key.id),
    )


def record_session_denied(db: AsyncSession, *, user: User | None) -> None:
    """A sign-in was refused.

    `user` is the account the address matched, or None when it matched none.
    Both are recorded, because repeated failures against one real account and a
    sweep across addresses that do not exist are different attacks and only the
    log tells them apart. Neither records the password, and the unknown case
    records no address at all.
    """
    if user is None:
        _add(
            db,
            org_id=None,
            actor_user_id=None,
            action=SESSION_DENIED,
            target=UNKNOWN_ACCOUNT_TARGET,
        )
        return
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=SESSION_DENIED,
        target=_target(USER_TARGET, user.id),
    )


def record_check_created(db: AsyncSession, *, check: Check, actor_user_id: uuid.UUID) -> None:
    """A new check exists, so our infrastructure will now fetch a URL somebody
    chose. That is worth a trail even though the check itself holds no secret:
    the request goes out under our addresses and our name."""
    _add(
        db,
        org_id=check.org_id,
        actor_user_id=actor_user_id,
        action=CHECK_CREATED,
        target=_target(CHECK_TARGET, check.id),
    )


def record_check_updated(db: AsyncSession, *, check: Check, actor_user_id: uuid.UUID) -> None:
    """An existing check changed. Editing a target is how a benign check becomes
    a request at something else, so an edit is as worth recording as a create."""
    _add(
        db,
        org_id=check.org_id,
        actor_user_id=actor_user_id,
        action=CHECK_UPDATED,
        target=_target(CHECK_TARGET, check.id),
    )


def record_check_deleted(
    db: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A check is gone. The row is taken by id rather than by object, because by
    the time this is called there is no row left to read one from."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=CHECK_DELETED,
        target=_target(CHECK_TARGET, check_id),
    )


def _add(
    db: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    target: str,
    actor_label: str | None = None,
    impersonation_context: uuid.UUID | None = None,
) -> None:
    """Put the entry in the caller's transaction. Nothing is committed here."""
    db.add(
        AuditLogEntry(
            org_id=org_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            impersonation_context=impersonation_context,
            action=action,
            target=target,
        )
    )


def _target(kind: str, identifier: uuid.UUID) -> str:
    """What an action was done to: a kind and an id, never a credential."""
    return f"{kind}:{identifier}"
