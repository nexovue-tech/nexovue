"""Who may change something, in one place.

**This changes no behaviour today, and that is what makes it safe to add now.**
Phase 1 is single-operator by decision (`phase-1-plan.md` §9): signup mints one
owner, there is no invite flow, and `users.role` is therefore `owner` for every
user that exists. Every policy below admits every current user.

It exists because the alternative is worse later. A role is read in exactly one
module today — `apikeys/service.py`, four call sites — while 47 routes are
served. When P1-76 brings a second person into an organization, or F26 brings
role enforcement, authorization stops being optional on all of them at once, and
retrofitting a cross-cutting concern into 47 places after another phase of
feature work is the change that never gets made.

**A dependency rather than a service-layer check**, because every route already
takes `CurrentIdentity` and this composes with it: a route swaps one annotation
and its body is untouched. The four existing checks inside `apikeys/service.py`
stay where they are — they guard *site* keys too, which is finer than a route.

**Named for what it protects, not for who may do it.** `MayConfigureIngestion`
survives F26 deciding that a `member` may configure ingestion;
`OwnerOrAdmin` would have to be renamed everywhere the day the answer changes.

`test_authorization_coverage.py` fails when a mutating route on an authenticated
router takes plain `CurrentIdentity`, so a route added next month is covered the
day it is written rather than the day someone remembers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final

from fastapi import Depends

from nexovue_api.auth.dependencies import CurrentIdentity, Identity
from nexovue_api.data.postgres.models import USER_ROLES
from nexovue_api.errors.exceptions import PermissionDeniedError

# The roles that may change a project's configuration. Matches
# `apikeys.service.ROLES_THAT_MAY_MANAGE_KEYS`, which predates this module and
# guards site keys as well as routes — restated rather than imported, because a
# policy module importing a domain service inverts the dependency.
ROLES_THAT_MAY_ADMINISTER: Final = frozenset({"owner", "admin"})


def require_role(*allowed: str) -> Callable[[Identity], Identity]:
    """Build a dependency that admits only these roles.

    The roles are checked against `USER_ROLES` at import time rather than at
    request time: a typo in a policy would otherwise be a gate that admits
    nobody, discovered by a customer rather than by a start-up failure.
    """
    unknown = sorted(set(allowed) - set(USER_ROLES))
    if unknown:
        raise ValueError(f"unknown role(s) in policy: {unknown}; known roles are {USER_ROLES}")

    permitted = frozenset(allowed)

    def guard(identity: CurrentIdentity) -> Identity:
        if identity.user.role not in permitted:
            raise PermissionDeniedError(_refusal(permitted))
        return identity

    return guard


def _refusal(permitted: frozenset[str]) -> str:
    """Say which roles may, not which role the caller holds.

    Telling a caller their own role back is fine; telling them the rule is what
    lets them ask the right person. Sorted so the message is stable.
    """
    named = (f"an {role}" if role[0] in "aeiou" else f"a {role}" for role in sorted(permitted))
    return f"Only {' or '.join(named)} can change this."


# The one policy Phase 1 needs. A second belongs here the moment two policies
# genuinely differ — not before, because a policy per route is a role system
# nobody designed.
MayAdminister = Annotated[Identity, Depends(require_role(*ROLES_THAT_MAY_ADMINISTER))]
