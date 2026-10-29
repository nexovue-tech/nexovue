"""`nexovue <role>`: which process the `nexovue` image runs (ADR-019; ready-to-deploy, T3).

One image carries the API, the scheduler and the console's files; the role is chosen by the start
command, never by building a different image. Each role replaces this process with its own, so
signals and the exit code reach the container runtime directly.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Final

# Where the image keeps the built console (deploy/images/nexovue.Dockerfile).
CONSOLE_FILES: Final = Path("/srv/web")

ROLES: Final[dict[str, list[str]]] = {
    "api": ["uvicorn", "nexovue_api.main:app", "--host", "0.0.0.0", "--port", "8000"],
    "scheduler-beat": ["python", "-m", "nexovue_scheduler", "beat"],
    "scheduler-light": ["python", "-m", "nexovue_scheduler", "worker", "--concurrency", "8"],
    "scheduler-deliveries": [
        *("python", "-m", "nexovue_scheduler", "worker"),
        *("--queues", "deliveries", "--concurrency", "4"),
    ],
    "scheduler-health": ["python", "-m", "nexovue_scheduler", "health"],
    "setup": ["python", "-m", "nexovue_api.setup"],
    "backup": ["python", "-m", "nexovue_api.backups"],
}

USAGE: Final = (
    f"usage: nexovue <role> [arguments]\nroles: {', '.join(ROLES)}, web-assets <directory>"
)


def command_for(role: str, extra: list[str]) -> list[str]:
    """The process a role runs. Extra arguments go to it, so an operator can tune a role."""
    if role not in ROLES:
        raise SystemExit(f"nexovue: unknown role {role!r}\n{USAGE}")
    return [*ROLES[role], *extra]


def copy_console(destination: Path, source: Path = CONSOLE_FILES) -> int:
    """Replace the destination's files with the console's, for the edge to serve.

    The directory is emptied first: a file a newer release removed must not stay servable.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for entry in destination.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return sum(1 for path in destination.rglob("*") if path.is_file())


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        print(USAGE, file=sys.stderr)
        return 2
    role, extra = arguments[0], arguments[1:]
    if role == "web-assets":
        if len(extra) != 1:
            raise SystemExit("nexovue: web-assets takes the directory to fill")
        copied = copy_console(Path(extra[0]))
        print(f"web-assets: {copied} console files copied to {extra[0]}")
        return 0
    command = command_for(role, extra)
    os.execvp(command[0], command)


if __name__ == "__main__":
    sys.exit(main())
