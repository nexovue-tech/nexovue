"""What the evidence job keeps only for a while (F3 A62): the hour's error-group read.

An explanation itself goes with its finding (`detection/retention.py`'s rule, through
the cascade). Read by `jobs/expiry.py`, whose sweep deletes in batches.
"""

from __future__ import annotations

from typing import Final

# A read is reused only inside its own minute; an hour leaves room for a late gather.
EXPIRED: Final = (
    (
        "evidence_error_group_reads",
        "org_id, project_id, read_minute",
        "created_at < now() - interval '1 hour'",
    ),
)
