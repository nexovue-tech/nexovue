"""Route gates over the one table of who may do what (`auth/permissions.py`).

Every mutating route declares the action it performs — `MayOperateChecks`,
`MayManageChannels` — and `ALLOWED` says which roles may perform each action.
The table is the specification's FR1 (`docs/features/team-and-api-access/`),
and nothing else in the API decides who may do something.

**Named for what it protects, not for who may do it.** Widening who may operate
checks is one line in `ALLOWED`; a route never names a role.

**A dependency, not a service-layer check**, because every route already takes
`CurrentIdentity` and this composes with it. It re-reads the caller's role on
every request (the identity is loaded per request), so a role change applies to
the very next request of every open session.

`test_authorization_coverage.py` fails when a mutating route declares no action,
or declares a different one than its checked-in table says — so a gate that
quietly widens is a failed build, not a review comment.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from nexovue_api.auth.dependencies import CurrentIdentity, Identity
from nexovue_api.auth.permissions import ALLOWED, Action, may
from nexovue_api.errors.exceptions import PermissionDeniedError


def require(action: Action) -> Callable[[Identity], Identity]:
    """Build a dependency that admits only the roles `action` allows."""

    def guard(identity: CurrentIdentity) -> Identity:
        if not may(identity.user.role, action):
            raise PermissionDeniedError(refusal(action))
        return identity

    return guard


def refusal(action: Action) -> str:
    """Say which roles may, not which role the caller holds.

    Telling a caller the rule is what lets them ask the right person. Sorted so
    the message is stable.
    """
    roles = sorted(ALLOWED[action])
    named = (f"an {role}" if role[0] in "aeiou" else f"a {role}" for role in roles)
    return f"Only {' or '.join(named)} can change this."


MayOperateChecks = Annotated[Identity, Depends(require(Action.OPERATE_CHECKS))]
MayOperateAlerts = Annotated[Identity, Depends(require(Action.OPERATE_ALERTS))]
MayUploadSourceMaps = Annotated[Identity, Depends(require(Action.UPLOAD_SOURCE_MAPS))]
MayManageChannels = Annotated[Identity, Depends(require(Action.MANAGE_CHANNELS))]
MayManageCheckSecrets = Annotated[Identity, Depends(require(Action.MANAGE_CHECK_SECRETS))]
MayConfigureProjects = Annotated[Identity, Depends(require(Action.CONFIGURE_PROJECTS))]
MayManageCredentials = Annotated[Identity, Depends(require(Action.MANAGE_CREDENTIALS))]
MayManageMembers = Annotated[Identity, Depends(require(Action.MANAGE_MEMBERS))]
MayReadAudit = Annotated[Identity, Depends(require(Action.READ_AUDIT))]
MayCommitSpend = Annotated[Identity, Depends(require(Action.COMMIT_SPEND))]
MayEndTheOrganization = Annotated[Identity, Depends(require(Action.END_ORGANIZATION))]
