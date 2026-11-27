from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from shard import files_in, shards

TESTS = Counter(
    {Path("a.py"): 50, Path("b.py"): 40, Path("c.py"): 30, Path("d.py"): 20, Path("e.py"): 10}
)


def test_the_collection_lines_count_each_file_s_tests() -> None:
    lines = [
        "apps/api/tests/test_a.py::test_one",
        "apps/api/tests/test_a.py::test_two[p]",
        "tools/x/test_b.py::TestThing::test_three",
        "",
        "3 tests collected in 0.01s",
    ]
    assert files_in(lines) == Counter(
        {Path("apps/api/tests/test_a.py"): 2, Path("tools/x/test_b.py"): 1}
    )


def test_every_file_is_in_exactly_one_shard() -> None:
    together = [path for each in shards(TESTS, 2) for path in each]
    assert sorted(together) == sorted(TESTS)
    assert len(together) == len(set(together))


def test_the_shards_hold_about_as_many_tests() -> None:
    held = [sum(TESTS[path] for path in each) for each in shards(TESTS, 2)]
    assert max(held) - min(held) <= max(TESTS.values())


def test_one_shard_is_every_file() -> None:
    assert shards(TESTS, 1) == [sorted(TESTS)]


def test_a_count_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        shards(Counter(), 0)
