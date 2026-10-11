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
from datetime import datetime
from typing import Any, Final

from nexovue_platform import erasure
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AlertRule,
    ApiKey,
    ApiToken,
    AuditLogEntry,
    Check,
    DeployEvent,
    Integration,
    NotificationChannel,
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
# The second factor going on and off (T64). Both are recorded because both change
# how hard the account is to take: arming one is a customer protecting
# themselves, and disarming one is either the same customer changing phones or
# somebody who already holds the password removing the obstacle.
SECOND_FACTOR_ENABLED: Final = "user.second_factor_enabled"
SECOND_FACTOR_DISABLED: Final = "user.second_factor_disabled"
# A recovery code was spent to sign in. This is the entry that matters most of
# the three: a code used by anybody but its owner is the first visible sign of a
# takeover that already has the password. Which code is deliberately not
# recorded — the row says that one was used, never which or what it was.
RECOVERY_CODE_USED: Final = "user.recovery_code_used"
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
# F18. A heartbeat token is a credential anybody holding it can use to mark a
# job healthy, so replacing one is recorded the way minting a key is.
CHECK_HEARTBEAT_TOKEN_ISSUED: Final = "check.heartbeat_token_issued"
CHECKS_IMPORTED: Final = "check.imported"
# A secret checks send on our infrastructure's behalf. The value is never
# recorded; the id of the row it lives in is.
CHECK_SECRET_WRITTEN: Final = "check_secret.written"
CHECK_SECRET_DELETED: Final = "check_secret.deleted"
MAINTENANCE_WINDOW_CREATED: Final = "maintenance_window.created"
MAINTENANCE_WINDOW_UPDATED: Final = "maintenance_window.updated"
MAINTENANCE_WINDOW_DELETED: Final = "maintenance_window.deleted"
# F21. A channel is where our infrastructure will send on the customer's behalf,
# and a rule decides who is woken; both are worth a trail (FR26). Never a secret.
NOTIFICATION_CHANNEL_CREATED: Final = "notification_channel.created"
NOTIFICATION_CHANNEL_UPDATED: Final = "notification_channel.updated"
NOTIFICATION_CHANNEL_MUTED: Final = "notification_channel.muted"
NOTIFICATION_CHANNEL_DELETED: Final = "notification_channel.deleted"
ALERT_RULE_CREATED: Final = "alert_rule.created"
ALERT_RULE_UPDATED: Final = "alert_rule.updated"
ALERT_RULE_MUTED: Final = "alert_rule.muted"
ALERT_RULE_DELETED: Final = "alert_rule.deleted"
API_KEY_CREATED: Final = "api_key.created"
API_KEY_REVOKED: Final = "api_key.revoked"
# F36. A read credential, named apart from the ingest ones: one writes and one
# reads, and an audit that called both "a key" would hide which.
API_TOKEN_CREATED: Final = "api_token.created"
API_TOKEN_REVOKED: Final = "api_token.revoked"
# Named apart from the secret kind, because "a key was minted" is a different
# event depending on which one: one grants server ingest, the other a browser's.
SITE_KEY_CREATED: Final = "site_key.created"
SITE_KEY_REVOKED: Final = "site_key.revoked"
SESSION_DENIED: Final = "session.denied"
# The platform gate (`docs/changes/super-admin-in-the-console/`): entering it,
# and failing to. A refusal is the more interesting of the two.
ADMIN_MODE_ENTERED: Final = "admin_mode.entered"
ADMIN_MODE_REFUSED: Final = "admin_mode.refused"
DEPLOY_RECORDED: Final = "deploy.recorded"
DEPLOY_WEBHOOK_SECRET_VIEWED: Final = "deploy.webhook_secret_viewed"
# The two halves of P1-77, both tenantless so they outlive the tenant. The
# scheduler writes the second when the nightly erasure is proved (ADR-014).
ORGANIZATION_DELETION_REQUESTED: Final = erasure.DELETION_REQUESTED_ACTION
ORGANIZATION_ERASED: Final = erasure.ERASED_ACTION
# P1-76. A project is an ingest surface, so creating and closing one is worth the
# same trail a key is; renaming is recorded for the reason `check.updated` is —
# an edit is how a thing that was reviewed becomes a different thing.
PROJECT_CREATED: Final = "project.created"
PROJECT_UPDATED: Final = "project.updated"
PROJECT_DELETED: Final = "project.deleted"
# The one guarded edit: switching a shipped masking rule off widens
# what leaves the customer's process, and it is the only edit here whose blast
# radius is other people's secrets.
PROJECT_MASKING_RULE_DISABLED: Final = "project.masking_rule_disabled"

MEMBER_INVITED: Final = "member.invited"
MEMBER_INVITE_RESENT: Final = "member.invite_resent"
MEMBER_INVITE_WITHDRAWN: Final = "member.invite_withdrawn"
MEMBER_JOINED: Final = "member.joined"
MEMBER_ROLE_CHANGED: Final = "member.role_changed"
MEMBER_REMOVED: Final = "member.removed"
# Deleting your own account (personal-data spec): the same end as a removal, with
# the person as its actor rather than an admin.
MEMBER_LEFT: Final = "member.left"
# A person downloaded what nexovue holds about them. Worth a trail because the
# file holds where they signed in from.
PERSONAL_DATA_EXPORTED: Final = "user.data_exported"
ORGANIZATION_ONBOARDED: Final = "organization.onboarded"
ORGANIZATION_PAYMENT_RECORDED: Final = "organization.payment_recorded"
ORGANIZATION_OWNER_LINK_SENT: Final = "organization.owner_link_sent"
ORGANIZATION_INGEST_SUSPENDED: Final = "organization.ingest_suspended"
ORGANIZATION_INGEST_RESUMED: Final = "organization.ingest_resumed"
ORGANIZATION_PLAN_GRANTED: Final = "organization.plan_granted"
ORGANIZATION_PLAN_GRANT_ENDED: Final = "organization.plan_grant_ended"
SUPPORT_ACCESS_GRANTED: Final = "support.access_granted"
SUPPORT_ACCESS_WITHDRAWN: Final = "support.access_withdrawn"
SUPPORT_VIEW_STARTED: Final = "support.view_started"
SUPPORT_VIEW_REQUEST: Final = "support.view_request"
SUPPORT_VIEW_ENDED: Final = "support.view_ended"

ORGANIZATION_TARGET: Final = "organization"
USER_TARGET: Final = "user"
API_KEY_TARGET: Final = "api_key"
API_TOKEN_TARGET: Final = "api_token"
SITE_KEY_TARGET: Final = "site_key"
CHECK_TARGET: Final = "check"
CHECK_SECRET_TARGET: Final = "check_secret"
MAINTENANCE_WINDOW_TARGET: Final = "maintenance_window"
NOTIFICATION_CHANNEL_TARGET: Final = "notification_channel"
ALERT_RULE_TARGET: Final = "alert_rule"
PROJECT_CHECKS_TARGET: Final = "project"
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


def record_second_factor_enabled(db: AsyncSession, *, user: User) -> None:
    """A second factor was confirmed and is now required at sign-in."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=SECOND_FACTOR_ENABLED,
        target=_target(USER_TARGET, user.id),
    )


def record_second_factor_disabled(db: AsyncSession, *, user: User) -> None:
    """The second factor was turned off, with the current password proved."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=SECOND_FACTOR_DISABLED,
        target=_target(USER_TARGET, user.id),
    )


def record_recovery_code_used(db: AsyncSession, *, user: User) -> None:
    """A recovery code signed somebody in. Nothing about the code is stored."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=RECOVERY_CODE_USED,
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

    One row, not one per session: what happened is a single write to
    `users.sessions_valid_from`, which ends them all, and that is what this records.
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


def record_admin_mode_entered(db: AsyncSession, *, user: User, until: datetime) -> None:
    """nexovue's operator re-authenticated and took the platform surface."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=ADMIN_MODE_ENTERED,
        target=_target(USER_TARGET, user.id),
        detail={"until": until.isoformat()},
    )


def record_admin_mode_refused(db: AsyncSession, *, user: User) -> None:
    """Somebody with the operator's session could not prove they were them."""
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=ADMIN_MODE_REFUSED,
        target=_target(USER_TARGET, user.id),
    )


def record_api_token_created(
    db: AsyncSession, *, token: ApiToken, actor_user_id: uuid.UUID
) -> None:
    """A read credential was minted, by whom, and what it may read.

    The detail carries the scope and the expiry, never the secret: an audit row
    is readable by every admin, and the secret was shown once to one person.
    """
    _add(
        db,
        org_id=token.org_id,
        actor_user_id=actor_user_id,
        action=API_TOKEN_CREATED,
        target=_target(API_TOKEN_TARGET, token.id),
        detail={
            "name": token.name,
            "project_id": str(token.project_id) if token.project_id else None,
            "expires_at": token.expires_at.isoformat(),
        },
    )


def record_api_token_revoked(
    db: AsyncSession, *, token: ApiToken, actor_user_id: uuid.UUID
) -> None:
    """A read credential was withdrawn, and by whom."""
    _add(
        db,
        org_id=token.org_id,
        actor_user_id=actor_user_id,
        action=API_TOKEN_REVOKED,
        target=_target(API_TOKEN_TARGET, token.id),
        detail={"name": token.name},
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


def record_notification_channel_created(
    db: AsyncSession, *, channel: NotificationChannel, actor_user_id: uuid.UUID
) -> None:
    """A new destination for this organization's alerts."""
    _add(
        db,
        org_id=channel.org_id,
        actor_user_id=actor_user_id,
        action=NOTIFICATION_CHANNEL_CREATED,
        target=_target(NOTIFICATION_CHANNEL_TARGET, channel.id),
    )


def record_alert_rule_created(
    db: AsyncSession, *, rule: AlertRule, actor_user_id: uuid.UUID
) -> None:
    """A new rule deciding who is told when something breaks. The default rule a
    project is created with is recorded too, in the name of whoever created it."""
    _add(
        db,
        org_id=rule.org_id,
        actor_user_id=actor_user_id,
        action=ALERT_RULE_CREATED,
        target=_target(ALERT_RULE_TARGET, rule.id),
    )


def record_notification_channel_updated(
    db: AsyncSession, *, channel: NotificationChannel, actor_user_id: uuid.UUID
) -> None:
    """Renamed, reconfigured, or its secret replaced — which, the row does not say:
    a secret's history is not worth keeping next to the rest."""
    _channel_event(db, NOTIFICATION_CHANNEL_UPDATED, channel, actor_user_id)


def record_notification_channel_muted(
    db: AsyncSession, *, channel: NotificationChannel, actor_user_id: uuid.UUID
) -> None:
    """Muted or unmuted: somebody decided alerts here may go unheard for a while."""
    _channel_event(db, NOTIFICATION_CHANNEL_MUTED, channel, actor_user_id)


def record_notification_channel_deleted(
    db: AsyncSession, *, channel: NotificationChannel, actor_user_id: uuid.UUID
) -> None:
    _channel_event(db, NOTIFICATION_CHANNEL_DELETED, channel, actor_user_id)


def record_alert_rule_updated(
    db: AsyncSession, *, rule: AlertRule, actor_user_id: uuid.UUID
) -> None:
    _rule_event(db, ALERT_RULE_UPDATED, rule, actor_user_id)


def record_alert_rule_muted(db: AsyncSession, *, rule: AlertRule, actor_user_id: uuid.UUID) -> None:
    _rule_event(db, ALERT_RULE_MUTED, rule, actor_user_id)


def record_alert_rule_deleted(
    db: AsyncSession, *, rule: AlertRule, actor_user_id: uuid.UUID
) -> None:
    _rule_event(db, ALERT_RULE_DELETED, rule, actor_user_id)


def _channel_event(
    db: AsyncSession, action: str, channel: NotificationChannel, actor_user_id: uuid.UUID
) -> None:
    _add(
        db,
        org_id=channel.org_id,
        actor_user_id=actor_user_id,
        action=action,
        target=_target(NOTIFICATION_CHANNEL_TARGET, channel.id),
    )


def _rule_event(db: AsyncSession, action: str, rule: AlertRule, actor_user_id: uuid.UUID) -> None:
    _add(
        db,
        org_id=rule.org_id,
        actor_user_id=actor_user_id,
        action=action,
        target=_target(ALERT_RULE_TARGET, rule.id),
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


def record_check_heartbeat_token_issued(
    db: AsyncSession, *, check: Check, actor_user_id: uuid.UUID
) -> None:
    """A heartbeat URL was minted or replaced; the previous one stops working."""
    _add(
        db,
        org_id=check.org_id,
        actor_user_id=actor_user_id,
        action=CHECK_HEARTBEAT_TOKEN_ISSUED,
        target=_target(CHECK_TARGET, check.id),
    )


def record_checks_imported(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A batch of checks arrived from a cURL command, OpenAPI file or collection.
    Each check also gets its own `check.created`; this says they came together."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=CHECKS_IMPORTED,
        target=_target(PROJECT_CHECKS_TARGET, project_id),
    )


def record_check_secret_written(
    db: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """A secret was stored or replaced. Never its value, never its name."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=CHECK_SECRET_WRITTEN,
        target=_target(CHECK_SECRET_TARGET, secret_id),
    )


def record_check_secret_deleted(
    db: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=CHECK_SECRET_DELETED,
        target=_target(CHECK_SECRET_TARGET, secret_id),
    )


def record_maintenance_window_changed(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    window_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: str,
) -> None:
    """A window decides when a failure is not believed, so each change is kept."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        target=_target(MAINTENANCE_WINDOW_TARGET, window_id),
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


def record_organization_deletion_requested(db: AsyncSession, *, org_id: uuid.UUID) -> None:
    """The owner asked for the tenant to be closed and erased.

    Tenantless, like the scheduler's `organization.erased` certificate: the
    request deletes every row naming the organization, and nothing would erase
    an entry written under it. It names an id and a time, and no person.
    """
    _add(
        db,
        org_id=None,
        actor_user_id=None,
        action=ORGANIZATION_DELETION_REQUESTED,
        target=erasure.organization_target(org_id),
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


# F26: people coming into and leaving an organization. `detail` carries what an
# owner reading the log needs and the target cannot say: the address an invite
# went to, a role before and after.


def record_member_invited(db: AsyncSession, *, member: User, actor_user_id: uuid.UUID) -> None:
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=actor_user_id,
        action=MEMBER_INVITED,
        target=_target(USER_TARGET, member.id),
        detail={"email": member.email, "role": member.role},
    )


def record_member_invite_resent(
    db: AsyncSession, *, member: User, actor_user_id: uuid.UUID
) -> None:
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=actor_user_id,
        action=MEMBER_INVITE_RESENT,
        target=_target(USER_TARGET, member.id),
        detail={"email": member.email},
    )


def record_member_invite_withdrawn(
    db: AsyncSession, *, member: User, actor_user_id: uuid.UUID
) -> None:
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=actor_user_id,
        action=MEMBER_INVITE_WITHDRAWN,
        target=_target(USER_TARGET, member.id),
        detail={"email": member.email},
    )


def record_member_joined(db: AsyncSession, *, member: User) -> None:
    """Written as the person who joined: accepting is their own act."""
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=member.id,
        action=MEMBER_JOINED,
        target=_target(USER_TARGET, member.id),
        detail={"role": member.role},
    )


def record_member_role_changed(
    db: AsyncSession, *, member: User, actor_user_id: uuid.UUID, before: str
) -> None:
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=actor_user_id,
        action=MEMBER_ROLE_CHANGED,
        target=_target(USER_TARGET, member.id),
        detail={"before": before, "after": member.role},
    )


def record_member_removed(db: AsyncSession, *, member: User, actor_user_id: uuid.UUID) -> None:
    """No address: a removed person is named by their id from here on."""
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=actor_user_id,
        action=MEMBER_REMOVED,
        target=_target(USER_TARGET, member.id),
        detail={"role": member.role},
    )


def record_member_left(db: AsyncSession, *, member: User) -> None:
    """Written as the person who left: deleting their account is their own act."""
    _add(
        db,
        org_id=member.org_id,
        actor_user_id=member.id,
        action=MEMBER_LEFT,
        target=_target(USER_TARGET, member.id),
        detail={"role": member.role},
    )


def record_personal_data_exported(db: AsyncSession, *, user: User) -> None:
    _add(
        db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=PERSONAL_DATA_EXPORTED,
        target=_target(USER_TARGET, user.id),
    )


def person_target(user_id: uuid.UUID) -> str:
    """The target every entry about one person carries."""
    return _target(USER_TARGET, user_id)


# F31: what nexovue staff do. The actor is a label, never a customer's user.


def record_organization_onboarded(
    db: AsyncSession, *, organization: Organization, owner_email: str, operator_label: str
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=operator_label,
        action=ORGANIZATION_ONBOARDED,
        target=_target(ORGANIZATION_TARGET, organization.id),
        detail={"owner": owner_email, "region": organization.region},
    )


def record_payment_recorded(
    db: AsyncSession,
    *,
    organization: Organization,
    payment_id: str,
    amount_minor: int,
    currency: str,
    operator_label: str,
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=operator_label,
        action=ORGANIZATION_PAYMENT_RECORDED,
        target=_target(ORGANIZATION_TARGET, organization.id),
        detail={"payment": payment_id, "amount_minor": amount_minor, "currency": currency},
    )


def record_owner_link_sent(db: AsyncSession, *, owner: User, operator_label: str) -> None:
    _add(
        db,
        org_id=owner.org_id,
        actor_user_id=None,
        actor_label=operator_label,
        action=ORGANIZATION_OWNER_LINK_SENT,
        target=_target(USER_TARGET, owner.id),
        detail={"email": owner.email},
    )


def record_ingest_suspended(
    db: AsyncSession, *, organization: Organization, reason: str, actor_label: str
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=actor_label,
        action=ORGANIZATION_INGEST_SUSPENDED,
        target=_target(ORGANIZATION_TARGET, organization.id),
        detail={"reason": reason},
    )


def record_ingest_resumed(
    db: AsyncSession, *, organization: Organization, actor_label: str
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=actor_label,
        action=ORGANIZATION_INGEST_RESUMED,
        target=_target(ORGANIZATION_TARGET, organization.id),
    )


def record_plan_granted(
    db: AsyncSession,
    *,
    organization: Organization,
    plan_slug: str,
    services: int,
    ends_at: str,
    reason: str,
    actor_label: str,
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=actor_label,
        action=ORGANIZATION_PLAN_GRANTED,
        target=_target(ORGANIZATION_TARGET, organization.id),
        detail={"plan": plan_slug, "services": services, "until": ends_at, "reason": reason},
    )


def record_plan_grant_ended(
    db: AsyncSession, *, organization: Organization, plan_slug: str, actor_label: str
) -> None:
    _add(
        db,
        org_id=organization.id,
        actor_user_id=None,
        actor_label=actor_label,
        action=ORGANIZATION_PLAN_GRANT_ENDED,
        target=_target(ORGANIZATION_TARGET, organization.id),
        detail={"plan": plan_slug},
    )


# F31: nexovue support viewing the console as a person, with the owner's consent.


def record_support_access_granted(
    db: AsyncSession, *, org_id: uuid.UUID, owner_id: uuid.UUID, until: str
) -> None:
    _add(
        db,
        org_id=org_id,
        actor_user_id=owner_id,
        action=SUPPORT_ACCESS_GRANTED,
        target=_target(ORGANIZATION_TARGET, org_id),
        detail={"until": until},
    )


def record_support_access_withdrawn(
    db: AsyncSession, *, org_id: uuid.UUID, owner_id: uuid.UUID
) -> None:
    _add(
        db,
        org_id=org_id,
        actor_user_id=owner_id,
        action=SUPPORT_ACCESS_WITHDRAWN,
        target=_target(ORGANIZATION_TARGET, org_id),
    )


def record_support_view_started(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    viewed: User,
    operator_label: str,
    reason: str,
    impersonation_id: uuid.UUID,
    until: str,
) -> None:
    _add(
        db,
        org_id=org_id,
        actor_user_id=None,
        actor_label=operator_label,
        impersonation_context=impersonation_id,
        action=SUPPORT_VIEW_STARTED,
        target=_target(USER_TARGET, viewed.id),
        detail={"reason": reason, "until": until, "viewed": viewed.name or viewed.email},
    )


def record_support_view_request(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    viewed_id: uuid.UUID,
    operator_label: str,
    impersonation_id: uuid.UUID,
    method: str,
    path: str,
) -> None:
    """One per request made while viewing as someone (FR29, AC30)."""
    _add(
        db,
        org_id=org_id,
        actor_user_id=None,
        actor_label=operator_label,
        impersonation_context=impersonation_id,
        action=SUPPORT_VIEW_REQUEST,
        target=_target(USER_TARGET, viewed_id),
        detail={"request": f"{method} {path}"},
    )


def record_support_view_ended(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    viewed_id: uuid.UUID,
    operator_label: str,
    impersonation_id: uuid.UUID,
) -> None:
    _add(
        db,
        org_id=org_id,
        actor_user_id=None,
        actor_label=operator_label,
        impersonation_context=impersonation_id,
        action=SUPPORT_VIEW_ENDED,
        target=_target(USER_TARGET, viewed_id),
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
    detail: dict[str, Any] | None = None,
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
            detail=detail,
        )
    )


def _target(kind: str, identifier: uuid.UUID) -> str:
    """What an action was done to: a kind and an id, never a credential."""
    return f"{kind}:{identifier}"
