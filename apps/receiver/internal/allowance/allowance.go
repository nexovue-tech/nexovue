// Package allowance answers one question on the ingest path: has this
// organization spent the volume its plan includes for the calendar month, at
// the door this export came through.
//
// **Where the allowance comes from depends on the tier.** A plan that puts a
// ceiling on the services it bills sets it — the free tier. A plan that does not
// is sized on the count the customer committed to when they authorised their
// mandate, which is a ceiling of their own: they chose it, they can see it, and
// they raise it themselves. A paid tier with no active mandate has nothing to
// size an allowance on and enforces nothing, which is not a ceiling of zero.
//
// It used to be the free tier's boundary and only the free tier's, on the
// reading that the plan never caps a customer to hold their bill down. That
// refusal still stands and this still honours it — the difference is that "we
// will not cap you to protect our margin" is no longer confused with "you have
// no limit at all".
//
// The allowance is one number for the organization, and where the plan says so
// the browser door carries a **cap** within it: a public site key is
// printed in the customer's page, so anyone who reads the source can spend at
// that door, and bounding what it can spend is what keeps the customer's backend
// reporting whatever happens to their website.
//
// A cap rather than a partition, and the difference is the whole design. A
// partition reserves the browser's share whether it is used or not, so a
// customer sending only server telemetry loses it — a quarter of the free tier
// withdrawn for a feature nobody can reach until P1-39 ships. A cap leaves the
// share available to the server door until a browser actually spends it. Both
// doors are still judged against the plan's whole allowance, so nothing sums
// past it and a customer cannot buy more month by changing entrance.
//
// The same read carries the operator's stop valve, which is not a billing
// boundary at all: `organizations.ingest_suspended_at` is how one runaway tenant
// is stopped while it is still well inside its allowance. It rides this lookup
// rather than one of its own because closing a valve must not cost the ingest
// path a second query, and because it then inherits the cache TTL — which is
// also how long it takes to bite.
//
// The month, the pooling and the arithmetic are one rule, written a second time
// in `nexovue_api.usage.billing` for the screen that shows it. Two languages, one
// policy: change one and the customer is told a number we do not enforce. The
// tests on both sides use the same figures for that reason.
package allowance

import (
	"context"
	"fmt"
	"time"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

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

// percentWhole is the denominator `plan_prices.browser_share_percent` is
// written in: the column holds a whole-number percentage.
const percentWhole = 100

// Usage is one organization's month as the control plane records it: what the
// plan row in force says, and what the meter has counted so far. It holds the
// row's own figures rather than a verdict, so the arithmetic that decides a
// refusal runs here and nowhere else.
//
// IncludedBytes is only meaningful when Enforced: a plan that records no
// allowance, or no ceiling on billed services, has no number here to be over.
type Usage struct {
	IncludedBytes int64
	Enforced      bool

	// BrowserSharePercent is how much of IncludedBytes the browser door may
	// spend, as the plan row records it, and Split says whether the row records
	// one at all. A row that records no share is pooled: both doors spend the
	// one allowance, which is how every row written before the split reads.
	BrowserSharePercent int64
	Split               bool

	// IngestedBytes is what the meter has counted for the month so far, across
	// both doors, and BrowserIngestedBytes the part of it that came through the
	// browser door. The meter reads Kafka at its own pace, so both lag the
	// door: the cap is a ceiling with slack behind it, never an exact byte
	// count.
	IngestedBytes        int64
	BrowserIngestedBytes int64

	// Found is false when no organization row answered, which after the auth
	// gate means the row went away underneath us.
	Found bool

	// Deleted is true when the organization row exists and carries a
	// `deleted_at`. Distinct from Found being false, because the gate refuses
	// one and admits the other.
	Deleted bool

	// IngestSuspended is the operator's stop valve: `ingest_suspended_at` set on
	// the organization row.
	//
	// **Operational, not billing.** An organization well inside its allowance
	// can be suspended, and a suspension is lifted by a person rather than by
	// the calendar. It rides this read rather than one of its own so that
	// closing the valve costs no lookup and inherits the same cache TTL — which
	// is also the delay before it bites, on every instance.
	IngestSuspended bool
}

// Exists implements cache.Value.
func (u Usage) Exists() bool { return u.Found }

// browserIncludedBytes is the browser door's budget: the plan's share of the
// whole, rounded down. Integer arithmetic, so the screen's Python computes the
// same byte from the same row.
func (u Usage) browserIncludedBytes() int64 {
	return u.IncludedBytes * u.BrowserSharePercent / percentWhole
}

// browserChargedBytes is what the browser door's spend takes out of the pool:
// what it actually spent, but never more than its cap.
//
// The `min` is the whole design in one line, and it is what makes the share a
// cap rather than a reservation.
//
//   - It is what it spent, so a browser share nobody uses is not withheld from
//     the server. A customer sending no browser telemetry reaches the plan's
//     whole allowance at the server door — under a partition they were refused
//     at 4.5 GiB of 6 GiB with the rest unreachable, and unreachable by anyone,
//     since the browser SDK is P1-39 and unbuilt.
//   - It is never more than the cap, so an overshoot cannot reach the server.
//     The meter lags the door — it reads Kafka at its own pace — so a burst
//     through a scraped site key genuinely can be metered past the cap before
//     the door starts refusing. Charging that overshoot to the pool would let
//     the scraped key close the server door, which is the exact failure this
//     whole mechanism exists to prevent.
func (u Usage) browserChargedBytes() int64 {
	if u.BrowserIngestedBytes < u.browserIncludedBytes() {
		return u.BrowserIngestedBytes
	}
	return u.browserIncludedBytes()
}

// serverIngestedBytes is what the server door has spent: everything the meter
// counted that it did not count as the browser's. Rows metered before the door
// was recorded carry no browser bytes, so they are read as the server's.
func (u Usage) serverIngestedBytes() int64 {
	return u.IngestedBytes - u.BrowserIngestedBytes
}

// chargedBytes is the month as the allowance counts it: every server byte, plus
// the browser's spend up to its cap and not past it.
//
// This is the one number both doors are judged against, and using the charged
// figure rather than the raw total is what lets the two guarantees hold at
// once. A raw total would mean a browser overshoot pushes the organization past
// its plan and closes the server door — handing the scraped key exactly the
// outcome the cap exists to deny.
func (u Usage) chargedBytes() int64 {
	return u.serverIngestedBytes() + u.browserChargedBytes()
}

// Budget names which allowance an export is judged against, and is what a
// refusal says ran out.
type Budget int

const (
	// Pooled is the plan's whole allowance. Every entrance but the browser
	// door spends it, split row or not.
	Pooled Budget = iota
	// Browser is the ceiling on what the browser door alone may spend. It is a
	// lid on one tap, not a second tank — see budgetFor.
	Browser
)

// budgetFor is the budget a door spends on this row.
//
// **The share is a cap on the browser door, not a partition of the plan.** The
// browser door is refused past its share; every other entrance is refused past
// the whole allowance, exactly as it was before any split existed.
//
// Splitting the plan into two boxes was the first shape and it was wrong: it
// reserved the browser's share whether or not the customer sent any browser
// telemetry, so an organization sending only server data was refused at 4.5 GiB
// of a 6 GiB plan with the remaining 1.5 GiB unreachable. Nobody could reach it
// — the browser SDK is P1-39 and unbuilt — so it was a quarter of the free tier
// withdrawn for a feature that does not exist yet.
//
// The cap keeps every property the partition was for, and the arithmetic that
// makes both hold at once is browserChargedBytes.
func (u Usage) budgetFor(door nexovuev1.Door) Budget {
	if u.Split && door == nexovuev1.Door_DOOR_RUM {
		return Browser
	}
	return Pooled
}

// spent reports whether the budget a door spends is used up.
//
// Strictly greater, so a plan that includes nothing still refuses only once
// something has been ingested, and an organization that has ingested nothing is
// never turned away by arithmetic on two zeroes.
//
// Three rules, and the order matters.
//
// A row recording no share has no browser accounting at all: one allowance, one
// number, which is what every row written before the split meant.
//
// Otherwise the browser door is refused past its own cap, and then **both**
// doors are refused past the plan's charged total. The second test is what
// stops the cap becoming a way to buy more month — without it an organization
// could spend the whole plan at the server door and a further share at the
// browser door. The first is what bounds the browser door on its own, so a
// scraped key stops at the cap however much room the plan has left.
func (u Usage) spent(budget Budget) bool {
	if !u.Enforced {
		return false
	}
	if !u.Split {
		return u.IngestedBytes > u.IncludedBytes
	}
	if budget == Browser && u.BrowserIngestedBytes > u.browserIncludedBytes() {
		return true
	}
	return u.chargedBytes() > u.IncludedBytes
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

// Cause names why the door must turn ingest away. One cause rather than a
// boolean each, because the three refusals are mutually exclusive and separate
// flags can be set to say something that cannot be true — a spent allowance on
// an organization that was also deleted.
//
// They are kept apart because each is a different conversation with a different
// ending: a spent allowance returns on the 1st, a deleted organization never
// does, and a suspension ends when a person ends it.
type Cause int

const (
	// Admitted is the only cause that lets the export through.
	Admitted Cause = iota
	// AllowanceSpent is the plan's included volume used up for the month, at
	// the budget this door spends. ResetsOn says when it comes back.
	AllowanceSpent
	// OrganizationGone is ingest arriving for a deleted organization.
	OrganizationGone
	// IngestSuspended is the operator's stop valve, closed by hand on this
	// organization. Not a billing boundary — see Usage.IngestSuspended.
	IngestSuspended
)

// Verdict is what the gate needs about one organization at one door.
type Verdict struct {
	// Cause is why ingest must be turned away, or Admitted.
	Cause Cause

	// Budget is the allowance that was judged, so the refusal can say which
	// one ran out. Only meaningful when Cause is AllowanceSpent.
	Budget Budget

	// ResetsOn is the day the allowance comes back. Only meaningful when Cause
	// is AllowanceSpent.
	ResetsOn time.Time
}

// Refused reports whether the door must turn this export away.
func (v Verdict) Refused() bool { return v.Cause != Admitted }

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

// Check reports whether this organization may still ingest through a door. The
// door is the one the envelope will carry, so the budget judged here is the
// budget the meter will charge.
//
// One read per organization serves both doors: the month is read whole and
// split here, which is what keeps a customer from buying more month by
// changing entrance.
//
// An error means the control plane could not be read. The caller decides what
// to do about that, because whether ingest fails open or closed is a policy
// question and not this package's to answer.
func (c *Checker) Check(ctx context.Context, orgID string, door nexovuev1.Door) (Verdict, error) {
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

	// A deleted organization is refused, and is not the same case as one we
	// cannot find. Reading them as one thing turned the cap off entirely:
	// the statement filtered `deleted_at IS NULL`, so a soft-deleted
	// organization returned no row, landed here, and was admitted — while its
	// keys went on authenticating, because the key lookups never join
	// `organizations`. Deleting an organization bought unlimited free ingest.
	//
	// Note what is still true: the key authenticates. Refusing it at the
	// credential is the wider fix and belongs with account deletion (M8), which
	// is also where revoking the keys belongs. This gate closes the volume hole
	// and nothing more.
	if held.Deleted {
		return Verdict{Cause: OrganizationGone}, nil
	}

	// An organization we genuinely cannot find has no allowance to be over. The
	// auth gate already proved the key, so this is a broken database rather
	// than a caller to refuse, and refusing telemetry over it would be the
	// wrong end.
	if !held.Found {
		return Verdict{}, nil
	}

	// The stop valve, and it is above the allowance on purpose: an operator
	// closes it precisely for a tenant that is *inside* its allowance and still
	// hurting the platform, so the refusal it produces has to be the one the
	// customer and the log both see. Refused at every door, because a tenant
	// stopped for what it is doing to the cluster is not stopped at one
	// entrance.
	if held.IngestSuspended {
		return Verdict{Cause: IngestSuspended}, nil
	}

	budget := held.budgetFor(door)
	if held.spent(budget) {
		return Verdict{Cause: AllowanceSpent, Budget: budget, ResetsOn: month.ResetsOn()}, nil
	}
	return Verdict{Budget: budget}, nil
}
