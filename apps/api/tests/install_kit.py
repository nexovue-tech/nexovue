"""Running one of `deploy/nexovue`'s shell functions from a test.

The script ends by calling `main`, so a test sources a copy without that last line,
placed beside a link to the backup helpers it sources from its own directory.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import nexovue_api

INSTALL_SCRIPT: Final = Path(nexovue_api.__file__).parents[4] / "deploy" / "nexovue"


def run_kit_function(
    workspace: Path, function: str, *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `function` with `arguments` (each quoted) and return what it printed."""
    kit = workspace / "kit"
    if not kit.exists():
        kit.mkdir()
        (kit / "backup").symlink_to(INSTALL_SCRIPT.parent / "backup")
        lines = INSTALL_SCRIPT.read_text().splitlines()
        assert lines[-1] == 'main "$@"', "the script's last line moved: follow it here"
        (kit / "nexovue").write_text("\n".join(lines[:-1]) + "\n")
    quoted = " ".join(f'"{argument}"' for argument in arguments)
    return subprocess.run(
        ["bash", "-c", f'source "{kit / "nexovue"}" && {function} {quoted}'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, **(environment or {})},
    )
