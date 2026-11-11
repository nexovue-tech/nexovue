"""Whether a new version gets AI wording, decided by the evidence job (F3 design, *The
wording decision and run*, *Wording states*; spec FR33 to FR35; plan T5.2).

Pure, and **no AI code** (AC5): the job decides `off`, `held`, `abstained` or `pending`
from the stored facts and the organization's settings, and adds the word job by name. Only
the AI role calls a provider.

In this order, for a version being written:

| What holds | State | Run? |
|---|---|---|
| AI off, unavailable, the project opted out, or causes still held (FR50) | `off` | No |
| The finding muted or flapping (FR35) | `held` | No |
| Only absences (FR33) | `abstained` | No |
| Material (FR34): the first version, a changed cause, a new possible cause | `pending` | Yes |
| Not material, over `pending` | `pending`: the older job sees a newer version and stops | Yes |
| Not material, over anything else | the earlier state and reason, its words carried | No |

Words checked against an earlier version are carried with that version's number, so the page
says they were written before the facts changed, and the API checks them against that row
(AC73, AC89).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.evidence import CAUSE_BAR_RECORDED, CAUSE_CAPABLE_KINDS, cause_released

OFF: Final = "off"
HELD: Final = "held"
ABSTAINED: Final = "abstained"
PENDING: Final = "pending"
# States whose words a later version carries: what a person saw stays until replaced.
_CARRIES_WORDS: Final = frozenset({"pending", "done", "failed", "over_limit"})


@dataclass(frozen=True, slots=True)
class Previous:
    """The latest version's wording, as the evidence job reads it."""

    items: Sequence[Mapping[str, Any]]
    likely_cause: Mapping[str, Any] | None
    state: str
    reason: str | None
    words: Any
    from_version: int | None


@dataclass(frozen=True, slots=True)
class Settings:
    """What the organization and install allow, read once per gather."""

    ai_on: bool
    opted_out: bool
    show_held_causes: bool
    call_seconds: int


@dataclass(frozen=True, slots=True)
class Decision:
    state: str
    reason: str | None = None
    words: Any = None
    from_version: int | None = None
    run: bool = False


def decide(
    *,
    items: Sequence[Mapping[str, Any]],
    likely_cause: Mapping[str, Any] | None,
    previous: Previous | None,
    muted_or_flapping: bool,
    settings: Settings,
) -> Decision:
    if not settings.ai_on or settings.opted_out or not _cause_allows_a_run(likely_cause, settings):
        return Decision(OFF)
    if muted_or_flapping:
        return Decision(HELD)
    if not any(item.get("kind") != "absence" for item in items):
        return Decision(ABSTAINED)
    carried = _carried(previous)
    if previous is None or material(items, likely_cause, previous):
        return Decision(PENDING, words=carried.words, from_version=carried.from_version, run=True)
    if previous.state == PENDING:
        return Decision(PENDING, words=carried.words, from_version=carried.from_version, run=True)
    return Decision(
        previous.state,
        reason=previous.reason,
        words=carried.words,
        from_version=carried.from_version,
    )


def material(
    items: Sequence[Mapping[str, Any]],
    likely_cause: Mapping[str, Any] | None,
    previous: Previous,
) -> bool:
    """FR34: the likely cause changed, or a fact that may be a cause is new."""
    if _cause_identity(likely_cause) != _cause_identity(previous.likely_cause):
        return True
    before = {item.get("id") for item in previous.items if item.get("kind") in CAUSE_CAPABLE_KINDS}
    now = {item.get("id") for item in items if item.get("kind") in CAUSE_CAPABLE_KINDS}
    return bool(now - before)


def _cause_identity(cause: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    if cause is None:
        return None
    return (cause.get("evidence_id"), cause.get("rule"), cause.get("strength"))


def _cause_allows_a_run(cause: Mapping[str, Any] | None, settings: Settings) -> bool:
    """FR50, A85: no run until the bar is met, nor for a cause whose rule is not released.
    The non-production override lets the founder judge the words (A60)."""
    if not (CAUSE_BAR_RECORDED or settings.show_held_causes):
        return False
    return cause is None or cause_released(cause.get("rule"), override=settings.show_held_causes)


@dataclass(frozen=True, slots=True)
class _Carried:
    words: Any = None
    from_version: int | None = None


def _carried(previous: Previous | None) -> _Carried:
    if previous is None or previous.state not in _CARRIES_WORDS or previous.words is None:
        return _Carried()
    return _Carried(words=previous.words, from_version=previous.from_version)
