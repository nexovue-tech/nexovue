"""Every package under `src/` reaches the wheel.

The build backend ships only the modules it is told about, and development
never notices a missing one: an editable install puts the whole source root on
the path, so the tests, the dev containers and the gate all pass while the built
image is missing a package. That is not hypothetical — it happened the day the
admin API briefly became a second package, and the wheel went out without it.

So this compares the packages that exist against the packages declared, and
fails the day someone adds a second.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

import nexovue_api

SOURCE_ROOT: Final = Path(nexovue_api.__file__).resolve().parents[1]
PYPROJECT: Final = SOURCE_ROOT.parent / "pyproject.toml"


def _packages_on_disk() -> set[str]:
    return {
        path.name
        for path in SOURCE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }


def _packages_declared() -> set[str]:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    name = declared["tool"]["uv"]["build-backend"].get("module-name")
    if name is None:
        # The backend then infers exactly one module from the distribution name.
        return {declared["project"]["name"].replace("-", "_")}
    return {name} if isinstance(name, str) else set(name)


def test_every_package_in_the_source_root_is_built() -> None:
    on_disk, declared = _packages_on_disk(), _packages_declared()

    assert on_disk == declared, (
        f"these packages exist but are not declared in pyproject.toml, so they are "
        f"absent from the wheel and from every deployed image: {sorted(on_disk - declared)}. "
        f"Declared but missing from disk: {sorted(declared - on_disk)}."
    )
