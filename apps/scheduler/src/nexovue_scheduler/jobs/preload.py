"""Imported once, by the forkserver every worker child is forked from (`worker.py`).

Every task module is imported here, before any child exists, so the children share those
pages copy-on-write instead of each importing its own: spawned, eight children held about
70 MiB of imports each, 560 MiB of `scheduler-light`'s 768 MiB (AC29). The imported objects
are then frozen, so a child's garbage collector never writes to their pages and unshares
them (`gc.freeze`).
"""

from __future__ import annotations

import gc

from nexovue_scheduler.jobs import registry

registry.registered()
gc.freeze()
