"""Prepare F20's demo project and print what to show (plan T11.4). Dev stack only.

Runs, in order: `tools/detection-seed/seed.py` for days of per-minute history (written as
rollups, so it is demo data and not ingest), then `fault.py` to make one service look broken so
the **real** detection job opens a finding. The signed deploy is a step for the person: it needs
the project's deploy secret, which this tool never reads (`docs/product/setup/deploys.md`).

    uv run python tools/dashboard-demo/demo.py            # print the plan
    uv run python tools/dashboard-demo/demo.py --run      # seed and fault, then print the script

Nothing here is a customer's data, and the demo must say so on screen.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

HERE: Final = Path(__file__).resolve().parent
SEED: Final = HERE.parent / "detection-seed" / "seed.py"
FAULT: Final = HERE.parent / "detection-seed" / "fault.py"
FAULTED_SERVICE: Final = "checkout"
# The seed counts weeks; one is the smallest it takes, and holds the three days the demo needs.
HISTORY_WEEKS: Final = 1


def plan(extra_seed_args: Sequence[str] = ()) -> list[list[str]]:
    """The commands, in order. The fault is last: detection needs the history first."""
    return [
        [sys.executable, str(SEED), "--weeks", str(HISTORY_WEEKS), *extra_seed_args],
        [sys.executable, str(FAULT), "--service", FAULTED_SERVICE, *extra_seed_args],
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", action="store_true", help="run the steps instead of printing them")
    parser.add_argument(
        "--clickhouse", help="the admin account's URL (default: the dev stack's, via the seed)"
    )
    parser.add_argument("--postgres", help="Postgres URL (default: the dev stack's)")
    arguments = parser.parse_args(argv)
    extra: list[str] = []
    if arguments.clickhouse:
        extra += ["--clickhouse", arguments.clickhouse]
    if arguments.postgres:
        extra += ["--postgres", arguments.postgres]
    for command in plan(extra):
        print("$", " ".join(command))
        if arguments.run:
            subprocess.run(command, check=True)
    print(f"\nThen follow {HERE / 'SCRIPT.md'} and sign {HERE / 'CHECKLIST.md'} into status.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
