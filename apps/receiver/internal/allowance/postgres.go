package allowance

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// One statement answers the gate: what the plan in force today includes, how it
// is split between the doors, and what the meter has counted this month at
// each.
//
// The rate is the one in force *today*, not on the month's last day — a
// repricing that starts on the 20th must not govern a month sitting on the 15th
// (ADR 0083). The bytes are the whole organization's, because the allowance is
// pooled across it; the browser's bytes are the part of them the meter counted
// through the browser door (ADR 0097).
//
// Both halves are LATERAL subqueries rather than joins with a GROUP BY, so an
// organization with no price row and no metered day still returns its row and
// is read as "nothing recorded" rather than as no answer at all.
const readMonthlyUsage = `
	SELECT price.included_bytes_per_service,
	       price.max_billed_services,
	       price.browser_share_percent,
	       COALESCE(metered.bytes, 0),
	       COALESCE(metered.browser_bytes, 0),
	       org.deleted_at IS NOT NULL
	FROM organizations org
	LEFT JOIN LATERAL (
		SELECT p.included_bytes_per_service, p.max_billed_services, p.browser_share_percent
		FROM plan_prices p
		WHERE p.plan_slug = org.plan AND p.effective_from <= $4
		ORDER BY p.effective_from DESC
		LIMIT 1
	) price ON true
	LEFT JOIN LATERAL (
		SELECT sum(u.bytes) AS bytes, sum(u.browser_bytes) AS browser_bytes
		FROM usage_daily u
		WHERE u.org_id = org.id AND u.day >= $2 AND u.day <= $3
	) metered ON true
	WHERE org.id = $1`

// The statement deliberately does not filter `deleted_at IS NULL`. It used to,
// and that is what turned the cap off for a deleted organization: no row came
// back, the gate read that as "cannot find" and admitted, and the keys went on
// authenticating because the key lookups never join `organizations`. Deletion
// is now a column the gate reads and refuses on.

// PostgresUsage reads the plan allowance out of the control plane.
type PostgresUsage struct {
	pool *pgxpool.Pool
}

// NewPostgresUsage returns a reader over an existing pool. The caller owns it.
func NewPostgresUsage(pool *pgxpool.Pool) *PostgresUsage {
	return &PostgresUsage{pool: pool}
}

// UsageFor implements Reader.
func (u *PostgresUsage) UsageFor(
	ctx context.Context,
	orgID string,
	month Month,
	on time.Time,
) (Usage, error) {
	var (
		perService      *int64
		maxServices     *int32
		browserShare    *int16
		ingested        int64
		browserIngested int64
		deleted         bool
	)
	err := u.pool.QueryRow(ctx, readMonthlyUsage,
		orgID, month.FirstDay, month.LastDay, on.UTC()).
		Scan(&perService, &maxServices, &browserShare, &ingested, &browserIngested, &deleted)

	switch {
	case errors.Is(err, pgx.ErrNoRows):
		// A miss is an answer, not a failure: it caches like any other.
		return Usage{Found: false}, nil
	case err != nil:
		return Usage{}, fmt.Errorf("querying the plan allowance: %w", err)
	}

	// Deletion is read before the plan is, because a deleted organization is
	// refused whatever its plan said.
	if deleted {
		return Usage{Found: true, Deleted: true}, nil
	}

	return usageFrom(perService, maxServices, browserShare, ingested, browserIngested), nil
}

// usageFrom turns what the statement returned into the gate's inputs. It is a
// function of its own rather than five lines inside the scan because this is
// the reading that decides whether anyone is refused, and it has to be provable
// without a database.
//
// **Either of the first two columns NULL means no allowance is recorded**, and
// nothing is enforced. `max_billed_services` is NULL on every paying tier, so
// this line is what keeps a customer who pays from ever being refused for
// volume — which would be a much larger failure than the hole this gate closes.
//
// The allowance itself is the ceiling's, not the running service count's, so
// the number is the plan's own: it is enforced at the door, and an allowance
// that moved with the service count would refuse one export and admit the next
// as a service came and went. `nexovue_api.usage.billing.included_bytes`
// computes the same figure for the screen; the two have to agree, or the
// customer is shown a limit we do not apply.
//
// An allowance recorded as zero is a plan that includes nothing, and refuses on
// the first byte. That is a different statement from NULL, and it is honoured.
//
// `browser_share_percent` NULL means the row records no split, and both doors
// spend the one allowance — every row seeded before ADR 0097 reads that way.
func usageFrom(
	perService *int64,
	maxServices *int32,
	browserShare *int16,
	ingested int64,
	browserIngested int64,
) Usage {
	if perService == nil || maxServices == nil {
		return Usage{IngestedBytes: ingested, BrowserIngestedBytes: browserIngested, Found: true}
	}
	usage := Usage{
		IncludedBytes:        *perService * int64(*maxServices),
		Enforced:             true,
		IngestedBytes:        ingested,
		BrowserIngestedBytes: browserIngested,
		Found:                true,
	}
	if browserShare != nil {
		usage.BrowserSharePercent = int64(*browserShare)
		usage.Split = true
	}
	return usage
}
