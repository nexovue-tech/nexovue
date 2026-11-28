"""The timeline seed's shape, without a store (F22 plan T8.4; D65).

The measurement itself runs against the dev stack and is recorded in `status.md` and
`docs/self-hosting/sizing.md`, because a number from a shared machine is a reading, not a test.
"""

from __future__ import annotations

import pytest
from seed import require_local
from timeline_seed import WORST, Shape


def test_the_worst_shape_is_a_full_case_at_every_limit_it_has() -> None:
    """D65's worst: a case kept 90 days by one person's act each time, a recurrence joining
    every 4 hours of it, and the 500 notes a case may hold (D33), each of 4,000 characters."""
    assert WORST.joined_findings == 90 * 24 // 4
    assert WORST.notes == 500
    assert WORST.note_characters == 4000


def test_the_rows_add_up() -> None:
    shape = Shape(
        joined_findings=10,
        events_per_joined=4,
        notes=3,
        background_findings=7,
        events_per_background=5,
        note_characters=10,
        other_organizations=2,
    )

    assert shape.case_events == 10 * 4 + 3 + Shape.ROOT_EVENTS
    assert shape.organization_events == shape.case_events + 7 * 5
    assert shape.all_events == shape.organization_events + 2 * 7 * 5


def test_it_refuses_a_store_that_is_not_this_machine() -> None:
    with pytest.raises(SystemExit):
        require_local("postgresql://nexovue:x@db.example.com:5432/nexovue", "Postgres")
