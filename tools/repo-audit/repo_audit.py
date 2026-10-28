"""Check the GitHub repository's settings against `expected.json`, and fail on any drift.

Settings changed by hand in a web page are invisible to review; this makes them checkable like code.
Run with `just repo-audit`; it reads `GITHUB_PERSONAL_ACCESS_TOKEN` and never prints it.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

REPOSITORY: Final = "nexovue-tech/nexovue"
EXPECTED: Final = Path(__file__).with_name("expected.json")

# A path whose answer is only a status: 204 means on, 404 means off.
ENABLED: Final = "enabled"

Fetch = Callable[[str], tuple[int, Any]]


def drift(expected: dict[str, Any], fetch: Fetch) -> list[str]:
    """Every setting that differs from what is expected, as one line each."""
    found: list[str] = []
    for path, wanted in expected.items():
        if path.startswith("_"):
            continue
        status, body = fetch(path)
        if wanted == ENABLED:
            if status != 204:
                found.append(f"{path or '/'}: expected enabled, the API answered {status}")
            continue
        if status != 200:
            found.append(f"{path or '/'}: the API answered {status}")
            continue
        for field, value in wanted.items():
            if body.get(field) != value:
                found.append(
                    f"{path or '/'} {field}: expected {value!r}, found {body.get(field)!r}"
                )
    return found


def github(token: str) -> Fetch:
    def fetch(path: str) -> tuple[int, Any]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            return error.code, None

    return fetch


def main() -> int:
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        print("GITHUB_PERSONAL_ACCESS_TOKEN is not set", file=sys.stderr)
        return 2
    found = drift(json.loads(EXPECTED.read_text()), github(token))
    for line in found:
        print(f"DRIFT {line}")
    print("repository settings match" if not found else f"{len(found)} setting(s) drifted")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
