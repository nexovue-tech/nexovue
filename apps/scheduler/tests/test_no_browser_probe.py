"""Browser checks are gone, everywhere they lived (spec FR39, AC31).

A search of the repository rather than a list of files: the failure this guards
against is a browser probe, queue, image or fixture surviving in a place nobody
thought to look. `tools/e2e/` is excluded on purpose — it drives Playwright to
test our own console, which has nothing to do with checks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]

# The justfile is not searched: its only Playwright line runs the console's own
# end-to-end suite, kept by the design for that reason.
SEARCHED: Final = ("apps", "packages", "deploy", "pyproject.toml")
EXCLUDED: Final = ("tools/e2e/", "node_modules/", "/gen/", "uv.lock", "pnpm-lock.yaml")

# What a browser check was made of. Each is specific enough that a match is the
# thing itself, not an unrelated use of the word "browser" (RUM's browser door).
REMOVED: Final = re.compile(
    r"run_browser_flow|BROWSER_TASK|QUEUE_BROWSER|scheduler-browser|Dockerfile\.browser"
    r"|browser_proxy|door2-synthetic-browser|probes\.browser|playwright"
    r'|CheckType = Literal\["http", "browser"\]|"browser" \| |value: "browser"',
    re.IGNORECASE,
)


def _tracked_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", *SEARCHED],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        REPOSITORY_ROOT / name
        for name in listed
        if not any(part in name for part in EXCLUDED) and (REPOSITORY_ROOT / name).is_file()
    ]


def test_the_search_reads_the_repository() -> None:
    files = _tracked_files()
    assert len(files) > 500, "the search found almost nothing, so it proves nothing"
    assert any(path.name == "tasks.py" for path in files)


def test_nothing_of_a_browser_check_remains() -> None:
    this_file = Path(__file__).resolve()
    found: list[str] = []
    for path in _tracked_files():
        if path == this_file:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found.extend(
            f"{path.relative_to(REPOSITORY_ROOT)}: {match.group(0)}"
            for match in REMOVED.finditer(text)
        )
    assert found == []
