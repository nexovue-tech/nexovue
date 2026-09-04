// Package allowance answers one question on the ingest path: has this
// organization spent the volume its plan includes for the calendar month.
//
// It is the free tier's boundary, and only the free tier's. A plan that puts no
// ceiling on the services it bills has no enforced allowance here, so no paying
// customer is ever refused for volume — see ADR 0093, which is what reconciles
// that with ADR 0083's refusal to cap a customer to hold their bill down.
//
// The month, the pooling and the arithmetic are ADR 0083's, and the same rule is
// written a second time in `nexovue_api.usage.billing` for the screen that shows
// it. Two languages, one policy: change one and the customer is told a number we
// do not enforce. The tests on both sides use the same figures for that reason.
package allowance

import (
	"context"
	"fmt"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/cache"
)

// Month is a calendar month, both ends included, in UTC — the same day boundary
// the usage meter counts on, so the sum below covers the days the screen shows.
type Month struct {
	FirstDay time.Time
	LastDay  time.Time
}

// monthOf is the calendar month containing a moment, in UTC.
func monthOf(at time.Time) Month {
	utc := at.UTC()
	first := time.Date(utc.Year(), utc.Month(), 1, 0, 0, 0, 0, time.UTC)
	return Month{FirstDay: first, LastDay: first.AddDate(0, 1, -1)}
}

// ResetsOn is the first day the allowance is whole again: the day after the
// month ends. It is the one fact a refused customer needs beyond the refusal.
func (m Month) ResetsOn() time.Time { return m.LastDay.AddDate(0, 0, 1) }

// Usage is one organization's month as the control plane records it.
//
// IncludedBytes is only meaningful when Enforced: a plan that records no
// allowance, or no ceiling on billed services, has no number here to be over.
type Usage struct {
	IncludedBytes int64
	Enforced      bool

	// IngestedBytes is what the meter has counted for the month so far. The
	// meter reads Kafka at its own pace, so this lags the door: the cap is a
	// ceiling with slack behind it, never an exact byte count.
	IngestedBytes int64

	// Found is false when no organization row answered, which after the auth
	// gate means the row went away underneath us.
	Found bool
}

// Exists implements cache.Value.
func (u Usage) Exists() bool { return u.Found }

// spent reports whether the month's included volume is used up.
//
// Strictly greater, so a plan that includes nothing still refuses only once
// something has been ingested, and an organization that has ingested nothing is
// never turned away by arithmetic on two zeroes.
func (u Usage) spent() bool {
	return u.Enforced && u.IngestedBytes > u.IncludedBytes
}

// Reader is the control-plane lookup. An interface because the store behind it
// is a detail of deployment, and because the cache and the arithmetic have to
// be testable without a Postgres instance.
type Reader interface {
	// UsageFor returns what one organization's plan includes for a month and
	// what the meter has counted in it, or a Usage with Found false when there
	// is no such organization.
	UsageFor(ctx context.Context, orgID string, month Month, on time.Time) (Usage, error)
}

// Options tune the in-process allowance cache.
type Options struct {
	// CacheTTL bounds two things at once: how far past its allowance an
	// organization can get before we notice, and how long an upgrade takes to
	// take effect. Both want it short.
	CacheTTL        time.Duration
	CacheMaxEntries int
}

// Verdict is what the gate needs about one organization.
type Verdict struct {
	// Spent is true when further ingest must be refused for the rest of the
	// month.
	Spent bool

	// ResetsOn is the day the allowance comes back. Only meaningful when Spent.
	ResetsOn time.Time
}

// Checker answers the gate from a short-lived copy of the control plane.
type Checker struct {
	usage Reader
	cache *cache.Cache[Usage]
	now   func() time.Time // replaced in tests; time.Now in production
}

// New builds a checker over a usage reader.
func New(usage Reader, opts Options) *Checker {
	return &Checker{
		usage: usage,
		cache: cache.New[Usage](opts.CacheTTL, opts.CacheMaxEntries),
		now:   time.Now,
	}
}

// Check reports whether this organization may still ingest.
//
// An error means the control plane could not be read. The caller decides what
// to do about that, because whether ingest fails open or closed is a policy
// question and not this package's to answer.
func (c *Checker) Check(ctx context.Context, orgID string) (Verdict, error) {
	now := c.now()
	month := monthOf(now)

	held, cached := c.cache.Lookup(orgID)
	if !cached {
		read, err := c.usage.UsageFor(ctx, orgID, month, now)
		if err != nil {
			return Verdict{}, fmt.Errorf("reading the plan allowance: %w", err)
		}
		// Misses cache too, so a vanished organization cannot hammer Postgres.
		c.cache.Store(orgID, read)
		held = read
	}

	// An organization we cannot find has no allowance to be over. The auth gate
	// already proved the key, so this is a broken database rather than a caller
	// to refuse, and refusing telemetry over it would be the wrong end.
	if !held.Found {
		return Verdict{}, nil
	}
	return Verdict{Spent: held.spent(), ResetsOn: month.ResetsOn()}, nil
}
