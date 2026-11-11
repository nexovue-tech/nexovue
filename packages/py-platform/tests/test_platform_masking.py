"""The Python port of the worker's default masking rules (F28 FR9, AC53).

The deploy webhook masks a sender's `metadata` before storing it, and the rules live in
the worker, so the port is held to the cases the Go test reads too
(`packages/contracts/fixtures/metadata-masking.json`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from nexovue_platform.masking import PLACEHOLDER, mask_flat

FIXTURE: Final = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "metadata-masking.json"
)
CASES: Final[list[dict[str, Any]]] = json.loads(FIXTURE.read_text())["cases"]


def test_the_shared_fixture_holds_cases() -> None:
    assert len(CASES) >= 20


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_port_masks_each_shared_case_as_the_worker_does(case: dict[str, Any]) -> None:
    assert mask_flat({case["key"]: case["value"]}) == {case["key"]: case["masked"]}


def test_the_placeholder_is_the_workers() -> None:
    assert PLACEHOLDER == "[masked]"


def test_every_entry_is_masked_on_its_own() -> None:
    masked = mask_flat({"password": "x", "branch": "main"})

    assert masked == {"password": PLACEHOLDER, "branch": "main"}


def test_a_value_with_two_kinds_of_credential_loses_both() -> None:
    value = "auth Bearer abcdefghijklmnopqrstuvwxyz0123 and https://u:pw@host.example/x"

    assert mask_flat({"note": value})["note"] == (
        "auth Bearer [masked] and https://u:[masked]@host.example/x"
    )
