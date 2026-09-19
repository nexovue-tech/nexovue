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
    UPLOAD_SOURCE_MAPS = "upload_source_maps"
    # Channels hold destinations and secrets, so they are not day-to-day work.
    MANAGE_CHANNELS = "manage_channels"
    MANAGE_CHECK_SECRETS = "manage_check_secrets"
    CONFIGURE_PROJECTS = "configure_projects"
    # Ingest keys, site keys, deploy webhook secrets and integrations.
    MANAGE_CREDENTIALS = "manage_credentials"
    MANAGE_MEMBERS = "manage_members"
    # Making or unmaking an owner is an owner's act (BR3).
    MANAGE_OWNERS = "manage_owners"
    READ_AUDIT = "read_audit"
    COMMIT_SPEND = "commit_spend"
    END_ORGANIZATION = "end_organization"


_EVERYONE_WHO_WORKS: Final = frozenset({"owner", "admin", "member"})
_ADMINISTRATORS: Final = frozenset({"owner", "admin"})
_OWNERS: Final = frozenset({"owner"})

# FR1. A viewer appears in no row: it reads every screen and changes nothing.
ALLOWED: Final[dict[Action, frozenset[str]]] = {
    Action.OPERATE_CHECKS: _EVERYONE_WHO_WORKS,
    Action.OPERATE_ALERTS: _EVERYONE_WHO_WORKS,
    Action.UPLOAD_SOURCE_MAPS: _EVERYONE_WHO_WORKS,
    Action.MANAGE_CHANNELS: _ADMINISTRATORS,
    Action.MANAGE_CHECK_SECRETS: _ADMINISTRATORS,
    Action.CONFIGURE_PROJECTS: _ADMINISTRATORS,
    Action.MANAGE_CREDENTIALS: _ADMINISTRATORS,
    Action.MANAGE_MEMBERS: _ADMINISTRATORS,
    Action.READ_AUDIT: _ADMINISTRATORS,
    # Signing a mandate that debits an account every month, or ending it, is the
    # account holder's own act; an admin who could cancel could stop ingestion.
    Action.COMMIT_SPEND: _OWNERS,
    # Deleting the tenant erases every other member's data and cannot be undone.
    Action.END_ORGANIZATION: _OWNERS,
    Action.MANAGE_OWNERS: _OWNERS,
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
