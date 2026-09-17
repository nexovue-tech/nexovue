"""The two ways a run can fail that are not the customer's service failing.

Both are raised while a run is being prepared or performed and turned into an
outcome by the runner. Neither retries and neither counts toward uptime (BR7).
"""

from __future__ import annotations


class MisconfiguredError(Exception):
    """The check cannot run as written: an undefined `{{name}}`, a secret that was
    deleted, a file that is no longer stored. The author's to fix."""


class ProberError(Exception):
    """We could not perform the run: storage unreachable, a key not configured.
    Ours to fix, and never reported as the customer being down."""
