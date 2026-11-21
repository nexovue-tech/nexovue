"""F4 plan T5.1: the knowledge records the API, the scheduler and migration 0076 share, held
equal, and the runbook ceiling's arithmetic (AC106, D77), offline."""

from __future__ import annotations

import importlib.util
import random
import re
from pathlib import Path
from types import ModuleType

import pytest
from nexovue_platform.assistant_masks import holds_credential, mask
from nexovue_platform.knowledge import (
    CHUNK_SPLIT_CHARACTERS,
    MAX_RUNBOOK_BYTES,
    MAX_RUNBOOK_CHUNKS,
    MAX_RUNBOOK_SERVICE_CHARACTERS,
    MAX_RUNBOOK_TITLE_CHARACTERS,
    SOURCE_KINDS,
    STALE_RUNBOOK_DAYS,
    chunk_runbook,
)

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "apps" / "api" / "migrations" / "versions" / "0076_knowledge.py"
RUNBOOK_AGE = ROOT / "apps" / "web" / "src" / "shared" / "runbook-age.ts"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("revision_0076", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_s_frozen_copies_equal_the_shared_records() -> None:
    frozen = _migration()
    assert ", ".join(f"'{kind}'" for kind in SOURCE_KINDS) == frozen._SOURCE_KINDS
    assert frozen._MAX_RUNBOOK_BYTES == MAX_RUNBOOK_BYTES
    assert frozen._MAX_TITLE == MAX_RUNBOOK_TITLE_CHARACTERS
    assert frozen._MAX_SERVICE == MAX_RUNBOOK_SERVICE_CHARACTERS
    assert frozen._MAX_CHUNK == CHUNK_SPLIT_CHARACTERS
    assert frozen._MAX_POSITION == MAX_RUNBOOK_CHUNKS - 1


def test_the_panel_and_the_screen_warn_at_the_same_age() -> None:
    source = RUNBOOK_AGE.read_text(encoding="utf-8")
    found = re.search(r"STALE_RUNBOOK_DAYS = (\d+);", source)
    assert found is not None and int(found.group(1)) == STALE_RUNBOOK_DAYS


@pytest.mark.parametrize(
    "text",
    [
        "# a\n" * (MAX_RUNBOOK_BYTES // 4),
        "#\n" * (MAX_RUNBOOK_BYTES // 2),
        "x" * MAX_RUNBOOK_BYTES,
        ("word " * 400 + "\n# h\n") * 15,
        "\n" * 10 + "# only\n" + "y " * 10_000,
    ],
    ids=["short headings", "bare hashes", "no headings", "long sections", "one heading"],
)
def test_a_runbook_within_its_size_makes_at_most_18_chunks(text: str) -> None:
    """AC106, D77: whatever its headings."""
    assert len(text.encode()) <= MAX_RUNBOOK_BYTES
    pieces = chunk_runbook(text)
    assert 1 <= len(pieces) <= MAX_RUNBOOK_CHUNKS
    assert all(1 <= len(piece) <= CHUNK_SPLIT_CHARACTERS for piece in pieces)


def test_random_runbooks_stay_under_the_ceiling_and_lose_no_word() -> None:
    picker = random.Random(20261121)
    for _ in range(200):
        lines = [
            picker.choice(["# ", "## ", "", "", "- "]) + "w" * picker.randint(0, 300)
            for _ in range(picker.randint(1, 400))
        ]
        text = "\n".join(lines)[:MAX_RUNBOOK_BYTES]
        pieces = chunk_runbook(text)
        assert len(pieces) <= MAX_RUNBOOK_CHUNKS
        assert "".join(pieces).replace("\n", "") == text.replace("\n", "")


def test_a_chunk_closes_at_a_heading_and_a_word_is_not_split() -> None:
    sections = ["## Pool\n" + "pool " * 50, "## Restart\n" + "restart " * 50]
    assert chunk_runbook("\n".join(sections)) == sections
    pieces = chunk_runbook("alpha " * 1_000)
    assert len(pieces) == 4
    assert all(piece.endswith("alpha ") for piece in pieces[:-1])


@pytest.mark.parametrize(
    ("text", "holds"),
    [
        ("Connect with password: hunter2", True),
        ("curl -H 'Authorization: Bearer abcdef0123456789'", True),
        ("postgres://admin:s3cret@db:5432/app", True),
        ("card 4111 1111 1111 1111", True),
        ("restart pgbouncer and watch the pool", False),
        ("mail oncall@example.com", False),
    ],
)
def test_the_save_warning_is_exactly_what_the_send_mask_hides(text: str, holds: bool) -> None:
    """AC69: one list, so the warning and the mask before sending cannot drift apart."""
    assert holds_credential(text) is holds
    assert (mask(text, mask_emails=False) != text) is holds
