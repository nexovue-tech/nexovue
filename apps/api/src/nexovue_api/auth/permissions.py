"""Who may do what: the table itself (F26, FR1).

Plain data and two lookups, with no web-framework import, so anything — the
identity response, a service, a test — can ask it without pulling in the
request dependencies. `auth/policy.py` turns it into route gates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from nexovue_api.data.postgres.models import USER_ROLES


class Action(StrEnum):
    """What a person may do. The value is what `GET /auth/me` sends the console."""

    # Checks, their runs, files, imports and heartbeat URLs.
    OPERATE_CHECKS = "operate_checks"
    # Alert rules (including muting) and maintenance windows.
    OPERATE_ALERTS = "operate_alerts"
    # Working a case on a finding (F22, FR34): take, assign, note, resolve,
    # reopen, link an issue.
    OPERATE_CASES = "operate_cases"
    UPLOAD_SOURCE_MAPS = "upload_source_maps"
    # Reliability targets (F19, D20): create, change, disable, delete, restore.
    # Every change is visible to all and a deletion can be undone for 30 days.
    CONFIGURE_SLOS = "configure_slos"
    # Dashboards (F20, BR7): create, change, delete, restore, and read a draft.
    # Any working role may change any dashboard, because a version remains to go back to.
    EDIT_DASHBOARDS = "edit_dashboards"
    # A finding's explanation (F3, A20): read its facts again now, and later ask for
    # plainer wording. Not alert operation: wording spends the customer's money.
    REQUEST_EXPLANATION = "request_explanation"
    # Channels hold destinations and secrets, so they are not day-to-day work.
    MANAGE_CHANNELS = "manage_channels"
    # Tracker connections (F22, FR34; foundation FD8): connect, test, replace a key and
    # disconnect GitHub or Jira. They hold repository access, so not day-to-day work.
    MANAGE_CONNECTIONS = "manage_connections"
    # An organization's AI provider (F3, foundation FD8): the connection, its key and test,
    # the limit, the switch and project opt-outs. It can send facts out, so not day-to-day.
    MANAGE_AI = "manage_ai"
    MANAGE_CHECK_SECRETS = "manage_check_secrets"
    CONFIGURE_PROJECTS = "configure_projects"
    # Anomaly detection's sensitivity, mutes and new normals, and every *anomaly
    # finding* alert rule (F6 FR23, D11): what decides whether detection is heard.
    CONFIGURE_DETECTION = "configure_detection"
    # Deleting another person's case note (F22, BR16). An author may always
    # delete their own, even as a viewer: the one exception to the viewer rule,
    # checked by the service on the route itself.
    DELETE_ANY_CASE_NOTE = "delete_any_case_note"
    # Removing a dashboard or one of its versions at once, past every way back (F20,
    # FR68, D47): the answer to a pasted secret, so it is an administrator's.
    PURGE_DASHBOARDS = "purge_dashboards"
    # Ingest keys, site keys, deploy webhook secrets and integrations.
    MANAGE_CREDENTIALS = "manage_credentials"
    # Read tokens for the organization's own data (F36, BR9).
    MANAGE_API_TOKENS = "manage_api_tokens"
    MANAGE_MEMBERS = "manage_members"
    # Making or unmaking an owner is an owner's act (BR3).
    MANAGE_OWNERS = "manage_owners"
    READ_AUDIT = "read_audit"
    # Letting nexovue support view the console as a person (F31, BR14).
    GRANT_SUPPORT = "grant_support"
    # Agreeing to send telemetry facts to an AI provider (F3 FR26): the account holder's
    # own act, like the bill and support access.
    CONSENT_AI_DISCLOSURE = "consent_ai_disclosure"
    COMMIT_SPEND = "commit_spend"
    END_ORGANIZATION = "end_organization"


_EVERYONE_WHO_WORKS: Final = frozenset({"owner", "admin", "member"})
_ADMINISTRATORS: Final = frozenset({"owner", "admin"})
_OWNERS: Final = frozenset({"owner"})

# FR1. A viewer appears in no row: it reads every screen and changes nothing.
ALLOWED: Final[dict[Action, frozenset[str]]] = {
    Action.OPERATE_CHECKS: _EVERYONE_WHO_WORKS,
    Action.OPERATE_ALERTS: _EVERYONE_WHO_WORKS,
    Action.OPERATE_CASES: _EVERYONE_WHO_WORKS,
    Action.UPLOAD_SOURCE_MAPS: _EVERYONE_WHO_WORKS,
    Action.CONFIGURE_SLOS: _EVERYONE_WHO_WORKS,
    Action.EDIT_DASHBOARDS: _EVERYONE_WHO_WORKS,
    Action.REQUEST_EXPLANATION: _EVERYONE_WHO_WORKS,
    Action.MANAGE_CHANNELS: _ADMINISTRATORS,
    Action.MANAGE_CONNECTIONS: _ADMINISTRATORS,
    Action.MANAGE_AI: _ADMINISTRATORS,
    Action.MANAGE_CHECK_SECRETS: _ADMINISTRATORS,
    Action.CONFIGURE_PROJECTS: _ADMINISTRATORS,
    Action.CONFIGURE_DETECTION: _ADMINISTRATORS,
    Action.DELETE_ANY_CASE_NOTE: _ADMINISTRATORS,
    Action.PURGE_DASHBOARDS: _ADMINISTRATORS,
    Action.MANAGE_CREDENTIALS: _ADMINISTRATORS,
    Action.MANAGE_API_TOKENS: _ADMINISTRATORS,
    Action.MANAGE_MEMBERS: _ADMINISTRATORS,
    Action.READ_AUDIT: _ADMINISTRATORS,
    # Signing a mandate that debits an account every month, or ending it, is the
    # account holder's own act; an admin who could cancel could stop ingestion.
    Action.COMMIT_SPEND: _OWNERS,
    # Deleting the tenant erases every other member's data and cannot be undone.
    Action.END_ORGANIZATION: _OWNERS,
    Action.MANAGE_OWNERS: _OWNERS,
    # Consent to be viewed as is the account holder's, like the bill.
    Action.GRANT_SUPPORT: _OWNERS,
    # An admin who could agree could send the organization's facts anywhere (F3 AC32).
    Action.CONSENT_AI_DISCLOSURE: _OWNERS,
}


def _check_table() -> None:
    """Every action has a row, and every row names only known roles.

    Checked at import rather than per request: a typo would otherwise be a gate
    that admits nobody, found by a customer rather than by a failed start-up.
    """
    missing = sorted(set(Action) - set(ALLOWED))
    if missing:
        raise ValueError(f"actions with no row in ALLOWED: {missing}")
    for action, roles in ALLOWED.items():
        unknown = sorted(roles - set(USER_ROLES))
        if unknown:
            raise ValueError(
                f"unknown role(s) for {action}: {unknown}; known roles are {USER_ROLES}"
            )


_check_table()


def may(role: str, action: Action) -> bool:
    return role in ALLOWED[action]


def permissions_of(role: str) -> list[Action]:
    """Every action a role may perform, in declaration order."""
    return [action for action in Action if may(role, action)]


# What a support view keeps (F31, BR15): reading. Every other action changes something.
READS: Final = frozenset({Action.READ_AUDIT})


def permissions_while_viewed(role: str) -> list[Action]:
    """What the console offers while nexovue support views as someone in `role`."""
    return [action for action in permissions_of(role) if action in READS]
