"""Erasing deleted organizations' telemetry and files, nightly (ADR-014)."""

from typing import Final

# Named here, not in `run`, so beat can schedule it without importing the
# ClickHouse and S3 clients it never uses.
ERASE_OWED_TASK: Final = "nexovue_scheduler.erasure.run.erase_owed_tonight"
