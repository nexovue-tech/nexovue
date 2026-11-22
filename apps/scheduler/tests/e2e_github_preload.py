"""The `connections` forkserver's preload for `e2e_worker.py`: the real one, then the simulated
GitHub's address in place of `api.github.com` (F22 T8.5)."""

from __future__ import annotations

import os

import nexovue_scheduler.jobs.preload_connections  # noqa: F401  the real preload, first
from nexovue_scheduler.connections import github

# `setattr`: the constant is `Final` for production code, and only this test launcher moves it.
setattr(github, "GITHUB_API", os.environ["E2E_GITHUB_API"])  # noqa: B010
