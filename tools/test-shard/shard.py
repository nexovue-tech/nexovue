"""Print one CI shard's Python test files, so `just check`'s Python half runs in parallel jobs.

The files are the ones pytest itself collects, so every exclusion it applies (markers,
`collect_ignore`, `testpaths`) holds here too. Files, not tests, are split: a file's tests share
fixtures (`--dist loadfile`). Each file goes to the shard with the fewest tests so far, largest
first, which balances well enough and never changes between runs of the same tree.

    uv run python tools/test-shard/shard.py 1 2   # the first of two shards
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def files_in(collection: Iterable[str]) -> Counter[Path]:
    """How many tests each file holds, from `pytest --collect-only -q` output lines."""
    return Counter(Path(line.split("::", 1)[0]) for line in collection if "::" in line)


def collected_files(root: Path = ROOT) -> Counter[Path]:
    """Every file holding at least one test a whole `pytest` run would select, with its count."""
    listing = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return files_in(listing.stdout.splitlines())


def shards(tests: Counter[Path], count: int) -> list[list[Path]]:
    """The files split into `count` lists holding about as many tests each."""
    if count < 1:
        raise ValueError("count must be at least 1")
    bins: list[list[Path]] = [[] for _ in range(count)]
    sizes = [0] * count
    for path in sorted(tests, key=lambda path: (-tests[path], str(path))):
        smallest = sizes.index(min(sizes))
        bins[smallest].append(path)
        sizes[smallest] += tests[path]
    return [sorted(each) for each in bins]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: shard.py INDEX COUNT  (INDEX from 1)", file=sys.stderr)
        return 2
    index, count = int(argv[0]), int(argv[1])
    if not 1 <= index <= count:
        print(f"INDEX must be between 1 and {count}", file=sys.stderr)
        return 2
    print(" ".join(str(path) for path in shards(collected_files(), count)[index - 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
