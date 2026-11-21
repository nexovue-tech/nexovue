"""Imported once, by the forkserver of a `--tasks connections` worker (`worker.py`).

The narrow twin of `preload.py` (F22 D80): only the connection tasks, so the process that
holds repository access has no ClickHouse, Kafka or object-store code in it, and the
children share these pages copy-on-write, frozen.
"""

from __future__ import annotations

import gc

from nexovue_scheduler.jobs import registry

registry.registered(registry.TASK_SETS["connections"])
gc.freeze()
