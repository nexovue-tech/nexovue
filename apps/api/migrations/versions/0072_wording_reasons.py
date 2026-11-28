"""Three more reasons a wording run can fail

F3 (`docs/features/ai-insights/`), plan Step 5 (T5.4).

**Why this change is required.** Step 5 writes AI wording, and a failed run says why to an
admin (spec, *States*). Migration 0066 froze the design's reasons; a run can also end with a
model or path the provider does not know (`not_found`, a 404) or a request it refused as
malformed (`bad_request`, a 400), which 0071 added to `ai_runs.outcome` for the connection
test; telling them apart from `bad_key` and `unavailable` tells an admin what to change. And
a fault in nexovue's own task writes `failed` before it is raised (AC54), as `error`, rather
than borrowing a provider's word for it.

**Affected store and schema.** Postgres: one check on `finding_explanations` replaced.

**Compatibility and backfill.** None: the check only widens. Replaced `NOT VALID`, then
validated, so the table is not locked against writes while its rows are checked.

**Rollback and recovery.** The downgrade restores 0066's list, and refuses while any row holds
one of the three new reasons, rather than lose what a run recorded.

**Tenant, retention, and security impact.** None: a closed word, mapped again when the API
reads it (A48).

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of `nexovue_platform.evidence.WORDING_REASONS`, now and before; the first is
# held equal by `test_evidence_vocabulary.py`.
_WORDING_REASONS = (
    "'died', 'expired', 'run_cap', 'too_long', 'bad_key', 'unavailable', 'limited', "
    "'refused', 'invalid', 'not_found', 'bad_request', 'error'"
)
_EARLIER_WORDING_REASONS = (
    "'died', 'expired', 'run_cap', 'too_long', 'bad_key', 'unavailable', 'limited', "
    "'refused', 'invalid'"
)
_CHECK = "ck_finding_explanations_wording_reason_is_known"


def _replace(reasons: str) -> None:
    op.execute(f"ALTER TABLE finding_explanations DROP CONSTRAINT {_CHECK}")
    op.execute(
        f"ALTER TABLE finding_explanations ADD CONSTRAINT {_CHECK} "
        f"CHECK (wording_reason IS NULL OR wording_reason IN ({reasons})) NOT VALID"
    )
    op.execute(f"ALTER TABLE finding_explanations VALIDATE CONSTRAINT {_CHECK}")


def upgrade() -> None:
    _replace(_WORDING_REASONS)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM finding_explanations
                WHERE wording_reason IN ('not_found', 'bad_request', 'error')
            ) THEN
                RAISE EXCEPTION 'rows hold a wording reason 0071 does not know; '
                    'going back would lose why their runs failed';
            END IF;
        END $$
        """
    )
    _replace(_EARLIER_WORDING_REASONS)
