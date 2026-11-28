"""Start the real scheduler worker, its children pointed at the simulated GitHub (F22 T8.5).

`python e2e_worker.py worker …` is `python -m nexovue_scheduler worker …`, the command a
container runs, with one change: the `connections` forkserver preloads `e2e_github_preload`,
which loads the real preload and then sets GitHub's address to `E2E_GITHUB_API`. Every job
runs in a child of that forkserver, so every call the role makes reaches the simulated
GitHub and nothing else. No setting moves GitHub's address in production
(`test_connections_tracker_seam.py`); this launcher lives with the tests and is never imported.
"""

from __future__ import annotations

import sys

from nexovue_scheduler import __main__ as scheduler
from nexovue_scheduler.jobs import registry

if __name__ == "__main__":
    registry.PRELOAD_OF["connections"] = "e2e_github_preload"
    scheduler.main(sys.argv[1:])
