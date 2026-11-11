"""Imported once, by the forkserver of a `--tasks insight` worker (`worker.py`).

The AI role's twin of `preload.py` (F3, design *The role*): only the `insight` tasks, so the
process that opens provider keys has no ClickHouse, Kafka or object-store code in it, and
the children share these pages copy-on-write, frozen.
"""

from __future__ import annotations

import gc

from nexovue_scheduler.jobs import registry

registry.registered(registry.TASK_SETS["insight"])
gc.freeze()
