package intake

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// recordRefusals is one minute's count for one receiver. GREATEST, so a retry
// after a write whose answer was lost never lowers what was recorded.
const recordRefusals = `
INSERT INTO intake_refusals (minute, receiver_id, reason, count)
VALUES ($1, $2, $3, $4)
ON CONFLICT (minute, receiver_id, reason)
DO UPDATE SET count = GREATEST(intake_refusals.count, EXCLUDED.count)`

// PostgresRefusals writes refusal minutes to the control plane.
type PostgresRefusals struct {
	pool *pgxpool.Pool
}

// NewPostgresRefusals returns a writer over an existing pool.
func NewPostgresRefusals(pool *pgxpool.Pool) *PostgresRefusals {
	return &PostgresRefusals{pool: pool}
}

// RecordRefusals upserts one minute's count.
func (p *PostgresRefusals) RecordRefusals(
	ctx context.Context, minute time.Time, receiverID, reason string, count int,
) error {
	if _, err := p.pool.Exec(ctx, recordRefusals, minute.UTC(), receiverID, reason, count); err != nil {
		return fmt.Errorf("recording intake_refusals: %w", err)
	}
	return nil
}
