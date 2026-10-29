"""`nexovue setup schema` (ready-to-deploy, T14): the newest Postgres migration an image carries.

`nexovue rollback` compares two versions' answers; an answer that lags the migrations would let a
rollback run old code against a schema it has never seen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import nexovue_api
from nexovue_api import setup

VERSIONS = Path(nexovue_api.__file__).parents[2] / "migrations" / "versions"


def test_the_schema_is_the_newest_migration(capsys: pytest.CaptureFixture[str]) -> None:
    newest = max(
        match.group(1)
        for path in VERSIONS.glob("*.py")
        if (match := re.search(r'^revision\b.*=\s*"([^"]+)"', path.read_text(), re.M))
    )

    assert setup.main(["schema"]) == 0
    assert capsys.readouterr().out == f"postgres {newest}\n"


def test_anything_else_after_setup_is_refused() -> None:
    with pytest.raises(SystemExit, match="usage: nexovue setup"):
        setup.main(["schemas"])
