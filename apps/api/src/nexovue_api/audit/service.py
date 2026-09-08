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
    DeployEvent,
    Integration,
    Organization,
    Project,
    SiteKey,
    User,
)

# `<entity>.<past tense>`, so a prefix match selects every event about one kind
# of thing. These strings are stored, so changing one is a migration.
ORGANIZATION_CREATED: Final = "organization.created"
SESSION_CREATED: Final = "session.created"
SESSION_ENDED: Final = "session.ended"
EMAIL_VERIFIED: Final = "user.email_verified"
PASSWORD_RESET_REQUESTED: Final = "user.password_reset_requested"
PASSWORD_RESET_COMPLETED: Final = "user.password_reset_completed"
# Distinct from a reset: this one was done by somebody already signed in who
# supplied the current password, which is a different story to read back.
PASSWORD_CHANGED: Final = "user.password_changed"
ALL_SESSIONS_ENDED: Final = "user.sessions_ended"
# The two halves of a change of address, kept apart because only the pair tells
# the story: a request with no completion is somebody who asked and could not
# prove the new inbox, and a completion is the moment the account's recovery
# channel moved. Neither records an address — see `record_email_changed`.
EMAIL_CHANGE_REQUESTED: Final = "user.email_change_requested"
EMAIL_CHANGED: Final = "user.email_changed"
DEPLOY_WEBHOOK_SECRET_CREATED: Final = "deploy.webhook_secret_created"
DEPLOY_WEBHOOK_SECRET_REVOKED: Final = "deploy.webhook_secret_revoked"
INTEGRATION_CREATED: Final = "integration.created"
INTEGRATION_DELETED: Final = "integration.deleted"
CHECK_CREATED: Final = "check.created"
CHECK_UPDATED: Final = "check.updated"
CHECK_DELETED: Final = "check.deleted"
API_KEY_CREATED: Final = "api_key.created"
API_KEY_REVOKED: Final = "api_key.revoked"
# Named apart from the secret kind, because "a key was minted" is a different
# event depending on which one: one grants server ingest, the other a browser's.
SITE_KEY_CREATED: Final = "site_key.created"
SITE_KEY_REVOKED: Final = "site_key.revoked"
SESSION_DENIED: Final = "session.denied"
DEPLOY_RECORDED: Final = "deploy.recorded"
DEPLOY_WEBHOOK_SECRET_VIEWED: Final = "deploy.webhook_secret_viewed"
# The two halves of P1-77. The first is an event in the tenant's own history and
# is erased with it; the second outlives the tenant on purpose — see
# `record_organization_erased`.
ORGANIZATION_DELETION_REQUESTED: Final = "organization.deletion_requested"
ORGANIZATION_ERASED: Final = "organization.erased"
# P1-76. A project is an ingest surface, so creating and closing one is worth the
# same trail a key is; renaming is recorded for the reason `check.updated` is —
# an edit is how a thing that was reviewed becomes a different thing.
PROJECT_CREATED: Final = "project.created"
PROJECT_UPDATED: Final = "project.updated"
PROJECT_DELETED: Final = "project.deleted"
# The one guarded edit in ADR 0105: switching a shipped masking rule off widens
# what leaves the customer's process, and it is the only edit here whose blast
# radius is other people's secrets.
PROJECT_MASKING_RULE_DISABLED: Final = "project.masking_rule_disabled"

ORGANIZATION_TARGET: Final = "organization"
USER_TARGET: Final = "user"
API_KEY_TARGET: Final = "api_key"
SITE_KEY_TARGET: Final = "site_key"
CHECK_TARGET: Final = "check"
INTEGRATION_TARGET: Final = "integration"
WEBHOOK_SECRET_TARGET: Final = "webhook_secret"
DEPLOY_TARGET: Final = "deploy"
PROJECT_TARGET: Final = "project"

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


def record_email_verified(db: AsyncSession, *, user: User) -> None:
    """The address was proven. Written once — a second click finds no token."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=EMAIL_VERIFIED,
        target=_target(USER_TARGET, user.id),
    )


def record_password_reset_requested(db: AsyncSession, *, user: User) -> None:
    """A reset link was mailed. Recorded only when an account matched — a request
    for an unknown address is somebody else's typo, and storing it would build a
    list of addresses somebody once guessed (see UNKNOWN_ACCOUNT_TARGET)."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=PASSWORD_RESET_REQUESTED,
        target=_target(USER_TARGET, user.id),
    )


def record_password_reset_completed(db: AsyncSession, *, user: User) -> None:
    """The password changed and every session ended. The pair matters: a reset
    with no matching request is somebody using a link that was not asked for."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=PASSWORD_RESET_COMPLETED,
        target=_target(USER_TARGET, user.id),
    )


def record_password_changed(db: AsyncSession, *, user: User) -> None:
    """A signed-in user changed their own password (P1-72).

    Kept apart from `PASSWORD_RESET_COMPLETED` because the two say different
    things about who was there: a reset is somebody who followed a mailed link,
    this is somebody who was already signed in and knew the old password. A
    change with no sign-in before it is the sequence worth noticing.
    """
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=PASSWORD_CHANGED,
        target=_target(USER_TARGET, user.id),
    )


def record_email_change_requested(db: AsyncSession, *, user: User) -> None:
    """A confirmation link was mailed to an address this account wants to move to.

    Recorded before anything changes, because this is the row that exists when a
    change is *attempted* and abandoned — which is what an account takeover that
    could not finish looks like from here.
    """
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=EMAIL_CHANGE_REQUESTED,
        target=_target(USER_TARGET, user.id),
    )


def record_email_changed(db: AsyncSession, *, user: User) -> None:
    """The account's address moved, and the new one was proven in the same step.

    **Neither address is recorded**, which follows the rule at the top of this
    file: a target names a row by its primary key. The old address is personal
    data about a person whose account no longer carries it, and the new one is
    on the row this entry already points at.
    """
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=EMAIL_CHANGED,
        target=_target(USER_TARGET, user.id),
    )


def record_all_sessions_ended(db: AsyncSession, *, user: User) -> None:
    """Every session on this account was ended at once (P1-72).

    One row, not one per session: there is nothing to enumerate — Redis keys
    sessions by token fingerprint — so what happened is a single write to
    `users.sessions_valid_from`, and that is what this records.
    """
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=ALL_SESSIONS_ENDED,
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


def record_site_key_created(
    db: AsyncSession, *, site_key: SiteKey, actor_user_id: uuid.UUID
) -> None:
    """A public browser credential was minted, and by whom.

    Recorded despite the key being non-secret: what is worth auditing is that a
    project gained a door, not that a secret changed hands.
    """
    _add(
        db,
        org_id=site_key.org_id,
        actor_user_id=actor_user_id,
        action=SITE_KEY_CREATED,
        target=_target(SITE_KEY_TARGET, site_key.id),
    )


def record_site_key_revoked(
    db: AsyncSession, *, site_key: SiteKey, actor_user_id: uuid.UUID
) -> None:
    """A public browser credential was withdrawn, and by whom."""
    _add(
        db,
        org_id=site_key.org_id,
        actor_user_id=actor_user_id,
        action=SITE_KEY_REVOKED,
        target=_target(SITE_KEY_TARGET, site_key.id),
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


def record_deploy_webhook_secret_created(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A new signing secret exists. The value is never recorded — only that one
    was minted, and by whom."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=DEPLOY_WEBHOOK_SECRET_CREATED,
        target=_target(PROJECT_TARGET, project_id),
    )


def record_deploy_webhook_secret_revoked(
    db: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A secret was withdrawn. Named by its row id, which discloses nothing."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=DEPLOY_WEBHOOK_SECRET_REVOKED,
        target=_target(WEBHOOK_SECRET_TARGET, secret_id),
    )


def record_integration_created(
    db: AsyncSession, *, integration: Integration, actor_user_id: uuid.UUID
) -> None:
    """A connector now exists, so we will assume a role in somebody's cloud
    account on a timer. The grant itself lives in their account, but who asked
    for it is ours to record."""
    _add(
        db,
        org_id=integration.org_id,
        actor_user_id=actor_user_id,
        action=INTEGRATION_CREATED,
        target=_target(INTEGRATION_TARGET, integration.id),
    )


def record_integration_deleted(
    db: AsyncSession, *, org_id: uuid.UUID, integration_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """Taken by id rather than by object: by the time this is called there is no
    row left to read one from."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=INTEGRATION_DELETED,
        target=_target(INTEGRATION_TARGET, integration_id),
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


def record_deploy_recorded(db: AsyncSession, *, event: DeployEvent) -> None:
    """A deploy arrived over the public webhook.

    No actor: the caller proved it holds the project's deploy secret, which says
    the request came from that project's CI and nothing at all about which
    person is behind it. Recorded anyway, because this is the one write in the
    control plane the internet can reach, and a burst of them is worth seeing.

    Only refused notifications are absent from this log: writing a row for every
    unsigned POST would let anyone fill the table from outside.
    """
    _add(
        db,
        org_id=event.org_id,
        actor_user_id=None,
        action=DEPLOY_RECORDED,
        target=_target(DEPLOY_TARGET, event.id),
    )


def record_deploy_webhook_secret_viewed(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """Somebody read a project's deploy secrets.

    Reading a credential is worth a trail even though it changes nothing: it is
    what an account takeover does before it starts forging deploys. The target
    is the project — the secret itself is never recorded.
    """
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=DEPLOY_WEBHOOK_SECRET_VIEWED,
        target=_target(PROJECT_TARGET, project_id),
    )


def record_organization_deletion_requested(
    db: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """The owner asked for the tenant to be closed and erased.

    Written under the organization, so it is erased along with everything else
    it names. `record_organization_erased` is what survives.
    """
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=ORGANIZATION_DELETION_REQUESTED,
        target=_target(ORGANIZATION_TARGET, org_id),
    )


def record_organization_erased(db: AsyncSession, *, org_id: uuid.UUID) -> None:
    """The erasure finished. The one entry deliberately left without a tenant.

    Every other row naming this organization has just been deleted, this one
    included if it carried `org_id` — the column cascades. A null one does not,
    which is what makes this the certificate that the erasure happened at all.
    It names an id and a time and nothing else: the actor is gone, and an
    organization id identifies no person.
    """
    _add(
        db,
        org_id=None,
        actor_user_id=None,
        action=ORGANIZATION_ERASED,
        target=_target(ORGANIZATION_TARGET, org_id),
    )


def record_project_created(db: AsyncSession, *, project: Project, actor_user_id: uuid.UUID) -> None:
    """A new environment exists, so there is a new place telemetry can be sent."""
    _add(
        db,
        org_id=project.org_id,
        actor_user_id=actor_user_id,
        action=PROJECT_CREATED,
        target=_target(PROJECT_TARGET, project.id),
    )


def record_project_updated(db: AsyncSession, *, project: Project, actor_user_id: uuid.UUID) -> None:
    """A project's name or slug changed. The old value is not recorded — the
    entry says who renamed what and when, and the row itself says what it is now."""
    _add(
        db,
        org_id=project.org_id,
        actor_user_id=actor_user_id,
        action=PROJECT_UPDATED,
        target=_target(PROJECT_TARGET, project.id),
    )


def record_project_deleted(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A project was closed and every credential it held was withdrawn."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=PROJECT_DELETED,
        target=_target(PROJECT_TARGET, project_id),
    )


def record_masking_rule_disabled(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    rule: str,
    actor_user_id: uuid.UUID,
) -> None:
    """A shipped masking rule was switched off, and which one.

    One row per rule, because "who widened what" is the question this log is
    asked, and a single row naming a list cannot be searched by rule.
    """
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=PROJECT_MASKING_RULE_DISABLED,
        target=masking_rule_target(project_id, rule),
    )


def masking_rule_target(project_id: uuid.UUID, rule: str) -> str:
    """The `target` a disabled-rule entry carries: the project, and the rule name.

    The one target that names more than a row's primary key. A rule name is a
    constant from `mask/rules.go` and discloses nothing — it is the name of a
    default we ship — and without it the entry would say a rule was turned off
    without saying which, which is the only fact worth recording here.
    """
    return f"{_target(PROJECT_TARGET, project_id)}:{rule}"


def erasure_certificate_target(org_id: uuid.UUID) -> str:
    """The `target` an `organization.erased` entry carries, for finding one.

    Public so the deletion can ask whether it already certified an organization
    without restating the format the entry was written with.
    """
    return _target(ORGANIZATION_TARGET, org_id)


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
