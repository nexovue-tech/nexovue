"""What a purge refuses to do, and what it reports when the store refuses.

Deliberately not mapped to HTTP here: nothing serves this package yet, and the
endpoint that will (P1-77) decides what a caller is told.
"""

from __future__ import annotations


class InvalidTenantError(ValueError):
    """The identifier handed to a purge is not an organization id.

    A purge names a tenant and then deletes everything under that name, so a
    malformed one is refused rather than run. Silently matching nothing would
    report a successful erasure that erased nothing.
    """

    def __init__(self, tenant_id: str) -> None:
        super().__init__(f"not an organization id, so nothing will be purged: {tenant_id!r}")


class PurgeFailedError(RuntimeError):
    """ClickHouse rejected or abandoned one of the deletions.

    Carries the tables, because "the purge failed" without them leaves the next
    person to work out which rows are still there.
    """

    def __init__(self, tenant_id: str, failures: dict[str, str]) -> None:
        detail = "; ".join(f"{table}: {reason}" for table, reason in sorted(failures.items()))
        super().__init__(f"purge of {tenant_id} failed on {len(failures)} table(s): {detail}")
        self.tenant_id = tenant_id
        self.failures = failures


class PurgeUnfinishedError(TimeoutError):
    """The deadline passed with rows still present.

    Not necessarily a failure — a mutation over a large table outlives any
    sensible wait, and the obligation is still recorded on the server. But a
    mutation that keeps failing and keeps being retried looks the same from
    outside, so whatever the store last complained about travels with it.
    """

    def __init__(
        self, tenant_id: str, remaining: dict[str, int], last_errors: dict[str, str] | None = None
    ) -> None:
        detail = ", ".join(f"{table}={rows}" for table, rows in sorted(remaining.items()))
        errors = last_errors or {}
        if errors:
            detail += "; last errors: " + "; ".join(
                f"{table}: {reason}" for table, reason in sorted(errors.items())
            )
        super().__init__(f"purge of {tenant_id} still has rows: {detail}")
        self.tenant_id = tenant_id
        self.remaining = remaining
        self.last_errors = errors
