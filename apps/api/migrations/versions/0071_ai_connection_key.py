"""An AI connection's sealed key, and two more ways a run can end

F3 (`docs/features/ai-insights/`), plan Step 4 (T4.6, T4.3).

**Why this change is required.** Step 3 created `ai_connections` without its key, because a
sealed column must join `keys.SEALED_COLUMNS` with the key ring that seals it, and the `ai`
ring is this step's. The key is sealed under `AI_SECRET_KEY`, purpose `ai-connection`,
bound to the organization, the connection and a hash of the base URL, so a key cannot open
for another address (AC34). It may be null: a local server may need none.

`ai_runs.outcome` gains `not_found` and `bad_request`, so a failed connection test can say
"model not found" apart from a refused key (spec, *Error cases*), and the run list keeps the
same closed word.

**Affected store and schema.** Postgres: one column added, one check replaced.

**Compatibility and backfill.** None: the column is nullable and no key exists yet. The
check is widened `NOT VALID`, then validated, so `ai_runs` is not locked against writes.

**Rollback and recovery.** Roll forward only, like 0070: dropping the column would erase
every organization's key with no way back. Going back past 0071 is a restore.

**Tenant, retention, and security impact.** The column is ciphertext; the API never returns
it, and the AI account reads it through a column grant (`insight_account.INSIGHT_GRANTS`).
It is deleted with its organization.

Revision ID: 0071
Revises: 0070
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A frozen copy of `nexovue_platform.ai_records.RUN_OUTCOMES`, held equal by
# `test_ai_vocabulary.py`.
_RUN_OUTCOMES = (
    "'started', 'done', 'abstained', 'unavailable', 'limited', 'bad_key', 'refused', "
    "'too_long', 'invalid', 'died', 'not_found', 'bad_request'"
)


def upgrade() -> None:
    op.add_column("ai_connections", sa.Column("sealed_key", sa.LargeBinary(), nullable=True))
    op.execute("ALTER TABLE ai_runs DROP CONSTRAINT ck_ai_runs_outcome_is_known")
    op.execute(
        f"ALTER TABLE ai_runs ADD CONSTRAINT ck_ai_runs_outcome_is_known "
        f"CHECK (outcome IN ({_RUN_OUTCOMES})) NOT VALID"
    )
    op.execute("ALTER TABLE ai_runs VALIDATE CONSTRAINT ck_ai_runs_outcome_is_known")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: dropping ai_connections.sealed_key would erase every "
        "organization's provider key. Going back past 0071 is a restore."
    )
