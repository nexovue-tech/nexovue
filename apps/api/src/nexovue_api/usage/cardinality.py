"""Service names that are really one service with an id stuck on the end.

The billing unit is distinct `service.name` per project per day, so a customer
whose name carries a pod id, a container id or a release tag inflates their own
bill by exactly that factor. Nothing here caps anything: a cap on an
observability product drops telemetry, and the customer then debugs an outage
with data we threw away. It warns, and it says what to change.

The advice it gives is the specification's own: `service.name` "MUST be the
same for all instances of horizontally scaled services", and what tells two
instances apart is `service.instance.id`.

**Why this will not false-accuse.** A single odd name is never reported. What
is reported is a *family*: three or more distinct names sharing a stem, each
ending in a separator followed by something that matches one of four narrow
shapes — a uuid, a run of at least eight hex characters, a Kubernetes pod
suffix, or a bare ordinal. One `checkout-7` is a service called `checkout-7`;
`checkout-0`, `checkout-1` and `checkout-2` together are a StatefulSet. The
Kubernetes shape leans on that generator's own alphabet, which omits vowels, so
ordinary words cannot match it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from typing import Final, NamedTuple

# The window a change is measured over, and the window before it: "twelve
# services, up from three last week".
RECENT_WINDOW_DAYS: Final = 7

# Below this a change is a project starting up, not a bill inflating. Twelve
# services in week one is a customer onboarding; the same twelve after a week
# of three is the thing worth saying out loud.
MIN_SERVICES_TO_EXAMINE: Final = 8

# What counts as a jump. Doubling inside a week is not organic growth for a
# number that only moves when someone deploys a new service.
JUMP_FACTOR: Final = 2

# Three, not two. Two names sharing a stem happen for honest reasons — `api`
# and `api-v2` — and the whole point of this module is to be quiet unless it is
# sure.
MIN_FAMILY_MEMBERS: Final = 3

# A stem shorter than this, or with no letter in it, is not a service name that
# had an id appended; it is an id all the way down, and naming it would say
# nothing a reader could act on.
MIN_STEM_LENGTH: Final = 2

# How many names are examined per project. A project in the middle of a real
# explosion has more names than this, and the families in the first few hundred
# are enough to name the cause — the query is a read on a page load, not a
# billing run.
MAX_NAMES_EXAMINED: Final = 500

# Examples quoted back to the customer. Enough to recognise the pattern.
MAX_EXAMPLES: Final = 3


class ProjectServiceCounts(NamedTuple):
    """One project's distinct service count, this window and the one before."""

    project_id: uuid.UUID
    recent: int
    previous: int


class ServiceFamily(NamedTuple):
    """Names that differ only by a trailing identifier."""

    stem: str
    # Which shape the suffixes matched — the word the screen uses to say what
    # it found.
    kind: str
    members: tuple[str, ...]


def has_jumped(counts: ProjectServiceCounts) -> bool:
    """Whether a project's service count moved enough to be worth showing.

    A project with nothing in the previous window has not jumped — it has
    started, and "ten services, up from none" is a sentence about a customer
    onboarding rather than about a bill inflating. A project that starts
    straight into pod ids is still caught, by the families rather than by this.
    """
    return (
        counts.previous > 0
        and counts.recent >= MIN_SERVICES_TO_EXAMINE
        and counts.recent >= counts.previous * JUMP_FACTOR
    )


def worth_examining(counts: ProjectServiceCounts) -> bool:
    """Whether to look at a project's actual names.

    A project with a handful of services cannot be the one whose names are
    inflating a bill, and reading its names would be a query for nothing.
    """
    return counts.recent >= MIN_SERVICES_TO_EXAMINE


def identifier_families(names: Iterable[str]) -> list[ServiceFamily]:
    """The families among a project's service names, largest first.

    Empty when nothing matches, which is the answer for almost every customer.
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for name in names:
        split = _identifier_suffix(name)
        if split is None:
            continue
        grouped.setdefault(split, set()).add(name)

    families = [
        ServiceFamily(stem=stem, kind=kind, members=tuple(sorted(members)))
        for (stem, kind), members in grouped.items()
        if len(members) >= MIN_FAMILY_MEMBERS
    ]
    families.sort(key=lambda family: (-len(family.members), family.stem))
    return families


# ------------------------------------------------------------------ the shapes

# Where an identifier is joined onto a name. A name with no separator in it has
# nothing appended — it is one name.
_SEPARATORS: Final = "-_."

# A canonical uuid at the end of a name. Matched whole rather than segment by
# segment, because its own hyphens would otherwise split it into pieces and
# every name would land in a family of one.
_TRAILING_UUID: Final = re.compile(
    r"[-_.][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# The shortest run of hex that is an identifier rather than a word. A docker
# short id is twelve; an abbreviated git sha is seven to forty.
_MIN_HEX_LENGTH: Final = 8

# Kubernetes generates pod and replica-set suffixes from this alphabet — the
# consonants and digits, vowels deliberately omitted "to reduce the chances of
# 'bad words' being formed" (k8s.io/apimachinery/pkg/util/rand). That omission
# is what makes the shape safe to match on: an English word cannot be spelled
# in it.
_POD_ALPHABET: Final = frozenset("bcdfghjklmnpqrstvwxz2456789")

# `<name>-<replicaset hash>-<pod suffix>`: the hash is five to ten characters,
# the suffix always five.
_POD_SUFFIX_LENGTH: Final = 5
_REPLICA_SET_HASH_LENGTHS: Final = range(5, 11)

# A StatefulSet ordinal. Bounded so a year or a port number in a name is not
# read as one.
_MAX_ORDINAL_DIGITS: Final = 6

UUID_SUFFIX: Final = "uuid"
HEX_SUFFIX: Final = "hex"
POD_SUFFIX: Final = "kubernetes-pod"
ORDINAL_SUFFIX: Final = "ordinal"


def _identifier_suffix(name: str) -> tuple[str, str] | None:
    """The stem and the shape of what was appended to it, if anything was.

    Most specific shape first: a uuid contains hex runs, and a Kubernetes pod
    name ends in a segment that a looser rule would read as something else.
    """
    trailing_uuid = _TRAILING_UUID.search(name)
    if trailing_uuid:
        return _stem_or_none(name[: trailing_uuid.start()], UUID_SUFFIX)

    split = _split_last_segment(name)
    if split is None:
        return None
    head, tail = split

    pod = _pod_stem(head, tail)
    if pod is not None:
        return _stem_or_none(pod, POD_SUFFIX)
    if len(tail) >= _MIN_HEX_LENGTH and _is_hex(tail):
        return _stem_or_none(head, HEX_SUFFIX)
    if len(tail) <= _MAX_ORDINAL_DIGITS and tail.isdigit():
        return _stem_or_none(head, ORDINAL_SUFFIX)
    return None


def _pod_stem(head: str, tail: str) -> str | None:
    """The deployment behind `<name>-<hash>-<suffix>`, or None if that is not it.

    Both generated segments have to be present and both have to be spellable in
    the generator's alphabet. One of them alone is not evidence.
    """
    if len(tail) != _POD_SUFFIX_LENGTH or not _in_pod_alphabet(tail):
        return None
    split = _split_last_segment(head)
    if split is None:
        return None
    stem, replica_set_hash = split
    if len(replica_set_hash) not in _REPLICA_SET_HASH_LENGTHS:
        return None
    return stem if _in_pod_alphabet(replica_set_hash) else None


def _split_last_segment(name: str) -> tuple[str, str] | None:
    """A name split at its last separator, or None when it has none."""
    cut = max(name.rfind(separator) for separator in _SEPARATORS)
    if cut <= 0 or cut == len(name) - 1:
        return None
    return name[:cut], name[cut + 1 :]


def _stem_or_none(stem: str, kind: str) -> tuple[str, str] | None:
    """A stem worth naming, or nothing. An all-digit stem names no service."""
    if len(stem) < MIN_STEM_LENGTH or not any(character.isalpha() for character in stem):
        return None
    return stem, kind


def _is_hex(text: str) -> bool:
    return all(character in "0123456789abcdefABCDEF" for character in text)


def _in_pod_alphabet(text: str) -> bool:
    return all(character in _POD_ALPHABET for character in text)


def examples_of(family: ServiceFamily) -> Sequence[str]:
    """The names quoted back, capped so a warning stays one sentence long."""
    return family.members[:MAX_EXAMPLES]
