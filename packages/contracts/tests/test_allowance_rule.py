"""The allowance rule, asserted from Python against the shared cases.

`apps/receiver/internal/allowance/allowance_rule_test.go` reads the same file and
states the same expectations independently. Two readers agreeing is what makes
"one rule in two languages" a fact rather than a comment.

**Why this file exists.** Both implementations carry a comment saying the other
must agree with them — *"the two have to agree or a customer is refused at a
limit the screen never showed them"* — and nothing checked. A divergence is
silent on both sides: the screen shows one allowance and the door applies
another, and the customer's telemetry stops at a number they were never told.

AC25.15, `docs/features/usage-billing/spec.md`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

from nexovue_api.data.postgres.models import PlanPrice
from nexovue_api.usage.billing import allowance_service_count

CASES_FILE: Final = (
    Path(__file__).resolve().parent.parent / "fixtures" / "allowance-service-count.json"
)


def _cases() -> list[dict[str, Any]]:
    loaded: dict[str, Any] = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = loaded["cases"]
    assert cases, "the shared cases file is empty, so neither language is being checked"
    return cases


def _price(max_billed_services: int | None) -> PlanPrice:
    """A price row that differs only in the field under test."""
    return PlanPrice(
        plan_slug="pro",
        effective_from=date(2026, 1, 1),
        currency="USD",
        price_per_service_minor_units=600,
        max_billed_services=max_billed_services,
        included_bytes_per_service=10 * 1024**3,
        browser_share_percent=None,
    )


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["name"]))
def test_python_sizes_the_allowance_as_the_shared_cases_say(case: dict[str, Any]) -> None:
    counted = allowance_service_count(
        _price(case["max_billed_services"]), case["services_committed"]
    )

    assert counted == case["expected"], (
        f"{case['name']}: Python sized the allowance on {counted}, the shared cases say "
        f"{case['expected']}. The receiver reads the same file — if this is the correct "
        "answer, both implementations and the file change together."
    )


def test_no_price_row_enforces_nothing() -> None:
    """Not in the shared file because the Go side has no equivalent of `None` for
    the whole row: it reads columns, and a missing row returns no columns at all.
    Held here so the Python behaviour is still stated somewhere."""
    assert allowance_service_count(None, 26) is None
