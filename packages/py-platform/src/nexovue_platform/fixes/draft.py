"""Whether a draft may be opened for a repository, before any GitHub call (spec FR22, BR13,
BR14, D58, D62, AC27a, AC60; design, *The check run*, step 3, *Fix App*; plan T7.4, T7.5).

Asked by the API's offer, by a `draft` check run and by the draft job again before it writes,
so all three refuse on one rule, in one order:

1. `drafts_not_allowed`: an owner has not allowed drafts for this repository (BR14).
2. `no_write_permission`: the App was not granted `contents: write` and
   `pull_requests: write` (design, *Fix App*, tiers).
3. `no_draft_support`: GitHub refused a draft here in the last 24 hours (AC27a; a guess).
4. `needs_likely_cause`: only a released likely cause from this service's own signed
   deploy reaches a draft (D58; design, *What reaches which result*).

`public_not_allowed` (BR13) needs GitHub's live answer on visibility, so it is decided after
the read token, by `public_refused`. Write access itself is off in code, before all of this
(`write_gate.py`, ADR-097). Pure: no store, no clock, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.records import (
    DRAFT_REFUSAL_HOLDS_FOR,
    DRAFTS_NOT_ALLOWED,
    NEEDS_LIKELY_CAUSE,
    NO_DRAFT_SUPPORT,
    NO_WRITE_PERMISSION,
)

# The likely-cause rules that reach a draft (D58): F3's own signed deploy, rare (rule 1) or
# more common (rule 3). A copy of F3's names, since this package reaches no evidence module
# here; `test_fix_draft_rules.py` holds them equal to `nexovue_platform.evidence`.
DRAFT_CAUSE_RULES: Final = frozenset({"signed_deploy", "signed_deploy_weak"})
# The App's grant a draft needs (design, *Fix App*, tiers), by GitHub's permission names.
_WRITE_GRANT: Final = {"contents": "write", "pull_requests": "write"}
_LEVELS: Final = {"read": 1, "write": 2, "admin": 3}


def has_write_grant(granted: Mapping[str, str] | None) -> bool:
    """Whether GitHub's stored answer grants both write permissions; unknown is no."""
    if not granted:
        return False
    return all(
        _LEVELS.get(str(granted.get(name)), 0) >= _LEVELS[level]
        for name, level in _WRITE_GRANT.items()
    )


def draft_refusal(
    *,
    drafts_allowed: bool,
    granted: Mapping[str, str] | None,
    draft_refused_at: datetime | None,
    signed_deploy_cause: bool,
    now: datetime,
) -> str | None:
    """The first of the four refusals that applies, or None."""
    if not drafts_allowed:
        return DRAFTS_NOT_ALLOWED
    if not has_write_grant(granted):
        return NO_WRITE_PERMISSION
    if draft_refused_at is not None and now - draft_refused_at < DRAFT_REFUSAL_HOLDS_FOR:
        return NO_DRAFT_SUPPORT
    if not signed_deploy_cause:
        return NEEDS_LIKELY_CAUSE
    return None


def public_refused(*, private: bool, public_allowed: bool) -> bool:
    """BR13: a public repository, by GitHub's live answer, needs an admin's allowance."""
    return not private and not public_allowed
