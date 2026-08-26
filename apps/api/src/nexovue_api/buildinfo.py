"""Which build of which service is running, reported by both probe endpoints."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import BaseModel

SERVICE_NAME: Final = "api"
UNKNOWN: Final = "unknown"

# The image build stamps these; Python has no equivalent of Go's VCS stamping.
REVISION_ENV_VAR: Final = "BUILD_REVISION"
BUILD_TIME_ENV_VAR: Final = "BUILD_TIME"

_GIT_TIMEOUT_SECONDS: Final = 2.0


class BuildInfo(BaseModel):
    """Identity of the running build."""

    service: str
    revision: str
    build_time: str


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    """Build facts from the environment, falling back to the git checkout in dev."""
    return BuildInfo(
        service=SERVICE_NAME,
        revision=os.environ.get(REVISION_ENV_VAR) or _revision_from_checkout(),
        build_time=os.environ.get(BUILD_TIME_ENV_VAR) or UNKNOWN,
    )


def _revision_from_checkout() -> str:
    """The commit of the working tree, so a developer's logs identify themselves."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if completed.returncode != 0:
        return UNKNOWN
    return completed.stdout.strip() or UNKNOWN
