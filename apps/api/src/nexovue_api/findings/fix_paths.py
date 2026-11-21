"""Candidate file paths from this finding's service's own stacks (spec FR11, FR11a, AC12d,
AC16; design, *Picking files*; plan T3.8).

**Every stack is attacker-writable** (A1): anyone with a browser key may name a file. So:

- only the stack text is read, **never the exception message** (AC16), and only path-like
  tokens ending in a file extension, with an optional line, are kept;
- at most 40 candidates of at most 200 characters, in frame order, refusing control
  characters and whitespace (guesses);
- third-party frames are dropped (`site-packages`, `node_modules`, system and Go roots,
  `vendor/`), and Java's and Kotlin's `at a.b.C.run(C.java:12)` becomes `a/b/C.java`;
- the occurrences are this finding's service's only (`recent_for_service`), so another
  service's frames never supply a candidate (AC12d).

The candidates are only names to look for: the pack job keeps a file only if it exists in
the mapped folder and passes every list (FR12). They live in `path_candidates` until the
job ends, and are cleared on every way out (AC12g).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from nexovue_platform.fixes.records import MAX_CANDIDATE_CHARACTERS, MAX_CANDIDATES

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingExplanation
from nexovue_api.errorgroups import service as errorgroups

# Third-party and system frames (design, *Picking files*).
THIRD_PARTY: Final = (
    "site-packages",
    "dist-packages",
    "node_modules",
    "/usr/lib",
    "/usr/local/lib",
    "/usr/local/go/",
    "/go/pkg/mod/",
    "vendor/",
    "<frozen",
)
# Room either side of the group's first sight, since F3 stores its first sight and not the
# window it read (status.md, *Open decisions*).
WINDOW_MARGIN: Final = timedelta(minutes=1)

_JVM_FRAME: Final = re.compile(r"\bat\s+([\w$.]+)\(([\w$-]+\.(?:java|kt)):(\d+)\)")
_PYTHON_FRAME: Final = re.compile(r'File "([^"\n]+)", line (\d+)')
_PATH_FRAME: Final = re.compile(
    r"((?:[A-Za-z]:)?[\w.@+~\-/\\]*[\w\-]\.[A-Za-z][A-Za-z0-9]{0,7})(?::(\d+))(?::\d+)?"
)
_BAD: Final = re.compile(r"[\x00-\x20\x7f]")

StackOutcome = Literal["stack_aged_out", "no_stack"]


@dataclass(frozen=True, slots=True)
class Candidate:
    path: str
    line: int | None

    def stored(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "from": "stack"}


def _usable(path: str) -> bool:
    return (
        0 < len(path) <= MAX_CANDIDATE_CHARACTERS
        and _BAD.search(path) is None
        and not any(marker in path for marker in THIRD_PARTY)
    )


def _jvm_path(qualified: str, file_name: str) -> str:
    """`com.acme.Handler.run` and `Handler.java` give `com/acme/Handler.java`."""
    parts = qualified.split(".")
    package = [part for part in parts[:-2] if part and part[0].islower()]
    return "/".join([*package, file_name])


def candidates_from_stacks(stacks: Iterable[str]) -> list[Candidate]:
    """Path-like tokens from stack text, in frame order, deduplicated, capped."""
    found: list[Candidate] = []
    seen: set[tuple[str, int | None]] = set()
    for stack in stacks:
        for line in stack.splitlines():
            for path, number in _frames_on(line):
                key = (path, number)
                if key in seen or not _usable(path):
                    continue
                seen.add(key)
                found.append(Candidate(path, number))
                if len(found) >= MAX_CANDIDATES:
                    return found
    return found


def _frames_on(line: str) -> list[tuple[str, int | None]]:
    if jvm := _JVM_FRAME.search(line):
        return [(_jvm_path(jvm.group(1), jvm.group(2)), int(jvm.group(3)))]
    if python := _PYTHON_FRAME.search(line):
        return [(python.group(1), int(python.group(2)))]
    return [(match.group(1), int(match.group(2))) for match in _PATH_FRAME.finditer(line)]


def error_group_of(
    explanation: FindingExplanation | None, service_name: str
) -> Mapping[str, Any] | None:
    """The `error_group` item F3 linked for this service, its first if several."""
    items = [] if explanation is None else explanation.evidence.get("items") or []
    groups = [
        item.get("values") or {}
        for item in items
        if isinstance(item, Mapping) and item.get("kind") == "error_group"
    ]
    mine = [group for group in groups if service_name in (group.get("services") or [])]
    return (mine or groups or [None])[0]


def _window(
    group: Mapping[str, Any], explanation: FindingExplanation
) -> tuple[datetime, datetime] | None:
    first_seen = group.get("first_seen")
    try:
        start = datetime.fromisoformat(first_seen) if isinstance(first_seen, str) else None
    except ValueError:
        return None
    if start is None or start.tzinfo is None:
        return None
    end = max(explanation.read_at, start + WINDOW_MARGIN)
    return start - WINDOW_MARGIN, end


async def stack_candidates(
    repository: TenantScopedRepository,
    finding: FindingRow,
    explanation: FindingExplanation | None,
) -> list[Candidate] | StackOutcome:
    """FR11, FR11a: this service's recent stacks, read in the window F3's evidence holds
    for the group, as candidates; or why there are none."""
    service_name = finding.service_name
    group = None if service_name is None else error_group_of(explanation, service_name)
    if service_name is None or group is None or explanation is None:
        return "no_stack"
    group_id = group.get("group_id")
    window = _window(group, explanation)
    if not isinstance(group_id, str) or window is None:
        return "no_stack"
    occurrences = await errorgroups.recent_for_service(
        repository,
        project_id=str(finding.project_id),
        group_id=group_id,
        service_name=service_name,
        from_time=window[0],
        to_time=window[1],
    )
    if occurrences is None:
        return "stack_aged_out"
    found = candidates_from_stacks(each.stacktrace for each in occurrences if each.stacktrace)
    return found or "no_stack"


def typed_candidates(paths: Sequence[str]) -> list[dict[str, Any]]:
    """A person's own paths, relative to the folder, marked as theirs (D4)."""
    return [{"path": path, "line": None, "from": "person"} for path in paths]
