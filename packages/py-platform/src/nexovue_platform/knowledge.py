"""F4's knowledge: what the API, the scheduler and migration 0076 share (design, section 6).

Chat searches a project's **chunks**: pieces of runbooks people wrote, and fact lines nexovue
composes from closed findings. Only these writers exist, each held by a test: the runbook
service on save (`chunk_runbook`, here so the cap's arithmetic sits beside the numbers it
rests on), and the scheduler's knowledge sweep for fact lines (Step 7 adds notes).

**The runbook ceiling.** A chunk closes at the first heading once it holds at least
`len(text) / 17` characters, and is split at `CHUNK_SPLIT_CHARACTERS`. A runbook is at most
32 KiB, so `len(text) / 17` is under the split size, every closed chunk holds at least that
minimum, and at most 17 close before the text runs out: 18 chunks at most, whatever its
headings (AC106). 200 runbooks at 18 is 3,600 chunks, so runbooks fit the project's cap by
construction, and a person's writing never fails because of a chat index.

The migration holds a frozen copy of `SOURCE_KINDS`; `test_knowledge_records.py` holds them
equal. Every number here is a guess the design names, which T6.4's measurement may move.
"""

from __future__ import annotations

import math
import re
from typing import Final, Literal

SourceKind = Literal["runbook", "fact"]
# Notes join with Step 7's `note_id` (plan T7.1), in a migration of their own.
SOURCE_KINDS: Final[tuple[SourceKind, ...]] = ("runbook", "fact")

# FR32 and D44.
MAX_RUNBOOK_BYTES: Final = 32 * 1024
MAX_RUNBOOKS_PER_PROJECT: Final = 200
MAX_RUNBOOK_TITLE_CHARACTERS: Final = 200
MAX_RUNBOOK_SERVICE_CHARACTERS: Final = 200
MAX_RUNBOOK_CHUNKS: Final = 18
CHUNK_SPLIT_CHARACTERS: Final = 2_000
# One upload action; each file is a runbook of its own (AC92 uploads 30).
MAX_UPLOAD_FILES: Final = 50
# The project's cap, and the fact lines' share of it (design, *The caps, in one place*).
MAX_PROJECT_CHUNKS: Final = 5_000
MAX_FACT_LINES: Final = 1_000
# A runbook's age past this shows in a warning tone (FR15, a guess); the panel and the
# runbooks screen hold the same number.
STALE_RUNBOOK_DAYS: Final = 180

# The proof above needs every closed chunk to reach the minimum before it is split.
if MAX_RUNBOOK_BYTES / (MAX_RUNBOOK_CHUNKS - 1) > CHUNK_SPLIT_CHARACTERS:
    raise RuntimeError("the split size must hold the runbook minimum, or the ceiling breaks")

KNOWLEDGE_SWEEP_TASK: Final = "nexovue_scheduler.knowledge.tasks.sweep"

# A Markdown heading: one to six `#` at the start of a line, then a space or the line's end.
_HEADING: Final = re.compile(r"#{1,6}(?:[ \t]|$)")


def chunk_runbook(text: str) -> list[str]:
    """A runbook's text in chunks, split by heading under the ceiling (FR30, D77)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    minimum = max(1, math.ceil(len(text) / (MAX_RUNBOOK_CHUNKS - 1)))
    closed: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if _HEADING.match(line) and len(current) >= minimum:
            closed.append(current)
            current = ""
        current += line
        while len(current) > CHUNK_SPLIT_CHARACTERS:
            cut = _split_at(current, minimum)
            closed.append(current[:cut])
            current = current[cut:]
    closed.append(current)
    return [piece.strip("\n") for piece in closed if piece.strip()]


def _split_at(text: str, minimum: int) -> int:
    """Where to cut an over-long chunk: the last whitespace before the split size, so a word
    stays whole for keyword search, unless that would leave the chunk under its minimum."""
    space = max(
        text.rfind(" ", 0, CHUNK_SPLIT_CHARACTERS), text.rfind("\n", 0, CHUNK_SPLIT_CHARACTERS)
    )
    return space + 1 if space + 1 >= minimum else CHUNK_SPLIT_CHARACTERS


# What the expiry sweep removes (design, *Other inventories*): a deleted project keeps no
# `project_retention` row and its deletion only sets `deleted_at`, so its runbooks and chunks
# go at the next sweep. Read by `jobs/expiry.py`, in `scheduler-light`, which loads no AI code.
_PROJECT_GONE: Final = """EXISTS (
        SELECT 1 FROM projects AS project
        JOIN organizations AS organization ON organization.id = project.org_id
        WHERE project.id = {table}.project_id
          AND project.org_id = {table}.org_id
          AND (project.deleted_at IS NOT NULL OR organization.deleted_at IS NOT NULL)
    )"""
EXPIRED: Final = (
    ("knowledge_chunks", "id", _PROJECT_GONE.format(table="knowledge_chunks")),
    ("runbooks", "id", _PROJECT_GONE.format(table="runbooks")),
)
