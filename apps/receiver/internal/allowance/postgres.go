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
// is split between the doors, what the meter has counted this month at each, and
// whether an operator has closed the stop valve on this organization.
//
// The rate is the one in force *today*, not on the month's last day — a
// repricing that starts on the 20th must not govern a month sitting on the 15th.
// The bytes are the whole organization's, because the allowance is pooled
// across it; the browser's bytes are the part of them the meter counted
// through the browser door.
//
// **The plan in force is a complimentary grant while one is active** (F31, FR27),
// else the paid plan: the grant's plan prices the allowance and its services
// size it. A grant ends by its own date, so nothing ever has to rewrite
// `organizations.plan` back — `usage/queries.plan_in_force` is the API's twin.
//
// Both halves are LATERAL subqueries rather than joins with a GROUP BY, so an
// organization with no price row and no metered day still returns its row and
// is read as "nothing recorded" rather than as no answer at all.
const readMonthlyUsage = `
	SELECT price.included_bytes_per_service,
	       price.max_billed_services,
	       price.browser_share_percent,
	       COALESCE(granted.services, committed.services_committed),
	       COALESCE(metered.bytes, 0),
	       COALESCE(metered.browser_bytes, 0),
	       org.deleted_at IS NOT NULL,
	       org.ingest_suspended_at IS NOT NULL
	FROM organizations org
	LEFT JOIN LATERAL (
		SELECT g.plan_slug, g.services
		FROM plan_grants g
		WHERE g.org_id = org.id
		  AND g.starts_at <= now() AND g.ends_at > now()
		  AND (g.ended_at IS NULL OR g.ended_at > now())
		ORDER BY g.starts_at DESC
		LIMIT 1
	) granted ON true
	LEFT JOIN LATERAL (
		SELECT p.included_bytes_per_service, p.max_billed_services, p.browser_share_percent
		FROM plan_prices p
		WHERE p.plan_slug = COALESCE(granted.plan_slug, org.plan) AND p.effective_from <= $4
		ORDER BY p.effective_from DESC
		LIMIT 1
	) price ON true
	LEFT JOIN LATERAL (
		SELECT s.services_committed
		FROM billing_subscriptions s
		WHERE s.org_id = org.id AND s.status = 'active'
		ORDER BY s.activated_at DESC NULLS LAST, s.created_at DESC
		LIMIT 1
	) committed ON true
	LEFT JOIN LATERAL (
		SELECT sum(u.bytes) AS bytes, sum(u.browser_bytes) AS browser_bytes
		FROM usage_daily u
		WHERE u.org_id = org.id AND u.day >= $2 AND u.day <= $3
	) metered ON true
	WHERE org.id = $1`

// `status = 'active'` is the whole of what makes the committed count real. A
// `pending` row is a checkout somebody started and may never finish, and a
// `cancelled` one is an arrangement that has ended — granting either an
// allowance would let an abandoned checkout raise a customer's ceiling for
// free. Ordered so the arrangement in force wins if two are somehow active.
//
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
		perService        *int64
		maxServices       *int32
		browserShare      *int16
		servicesCommitted *int32
		ingested          int64
		browserIngested   int64
		deleted           bool
		suspended         bool
	)
	err := u.pool.QueryRow(ctx, readMonthlyUsage,
		orgID, month.FirstDay, month.LastDay, on.UTC()).
		Scan(&perService, &maxServices, &browserShare, &servicesCommitted,
			&ingested, &browserIngested, &deleted, &suspended)

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

	// `ingest_suspended_at` NULL is the ordinary state, and that is the
	// deliberate reading: the valve is closed only by an explicit act, so a
	// column nobody has written cannot stop a tenant. Any non-NULL value
	// suspends — the timestamp records *when* an operator closed it and is
	// deliberately not compared with now, because reading a future date as
	// "not yet" would be a scheduling feature nobody asked for and would turn a
	// mistyped year into a valve that silently never closes.
	usage := usageFrom(
		perService, maxServices, browserShare, servicesCommitted, ingested, browserIngested)
	usage.IngestSuspended = suspended
	return usage, nil
}

// allowanceServiceCount is the count the month's allowance is sized on, or nil
// when nothing sizes it. **This is the rule, and it is written once here and
// once in `nexovue_api.usage.billing`. The two have to agree or a customer is
// refused at a limit the screen never showed them.**
//
// A plan that caps the services it bills sets the count itself: the allowance
// is the cap's, not the running count's, so three services' worth on Free
// whether one service is running or three. An allowance that moved with the
// service count would refuse one export and admit the next as a service came
// and went, and would tell a customer refused at 2 GiB that starting a second
// service is the remedy.
//
// A plan with no cap — every paying tier — is sized on what the customer
// committed to when they authorised the mandate. That is the customer's own
// ceiling: they chose it, can see it, and raise it themselves. Before that
// column existed this returned nothing here, which meant **a paying customer
// had no limit at all** while every free one did.
//
// Nil is "nothing recorded" and enforces nothing: an unpriced tier, or a paid
// tier whose subscription is not active. Deliberately not zero, which is a plan
// that includes nothing and refuses on the first byte — a real and different
// statement, and one this honours where a row actually says it.
func allowanceServiceCount(maxServices, servicesCommitted *int32) *int32 {
	if maxServices != nil {
		return maxServices
	}
	return servicesCommitted
}

// usageFrom turns what the statement returned into the gate's inputs. It is a
// function of its own rather than five lines inside the scan because this is
// the reading that decides whether anyone is refused, and it has to be provable
// without a database.
//
// **No rate, or nothing to size the allowance on, means nothing is enforced.**
// The second half of that used to read "`max_billed_services` is NULL on every
// paying tier, so this is what keeps a customer who pays from ever being
// refused for volume". That was true and it was the hole: it kept them from
// being refused *at any volume whatsoever*. A paid tier is now sized on the
// customer's own committed count, and a paid tier with no active subscription
// is still unenforced — there is no ceiling to apply, not a ceiling of zero.
//
// An allowance recorded as zero is a plan that includes nothing, and refuses on
// the first byte. That is a different statement from NULL, and it is honoured.
//
// `browser_share_percent` NULL means the row records no split, and both doors
// spend the one allowance — every row seeded before the split reads that way.
func usageFrom(
	perService *int64,
	maxServices *int32,
	browserShare *int16,
	servicesCommitted *int32,
	ingested int64,
	browserIngested int64,
) Usage {
	services := allowanceServiceCount(maxServices, servicesCommitted)
	if perService == nil || services == nil {
		return Usage{IngestedBytes: ingested, BrowserIngestedBytes: browserIngested, Found: true}
	}
	usage := Usage{
		IncludedBytes:        *perService * int64(*services),
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
