package allowance

import (
	"context"
	"errors"
	"testing"
	"time"
)

const orgID = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"

// The free tier as migration 0015 seeds it: 2 GiB per billed service, three
// billed services. The Go gate and `nexovue_api.usage.billing` compute the same
// 6 GiB from that row, and both suites use these figures so a divergence shows
// up as a failing number rather than as a customer told a limit we do not apply.
const (
	gib          = int64(1) << 30
	freeIncluded = 3 * 2 * gib
)

// fakeUsage stands in for the control plane and counts how often it is read.
type fakeUsage struct {
	usage Usage
	err   error
	reads int
	// month and on record what the checker asked for.
	month Month
	on    time.Time
}

func (f *fakeUsage) UsageFor(_ context.Context, _ string, month Month, on time.Time) (Usage, error) {
	f.reads++
	f.month, f.on = month, on
	if f.err != nil {
		return Usage{}, f.err
	}
	return f.usage, nil
}

func newChecker(usage Reader) *Checker {
	return New(usage, Options{CacheTTL: time.Minute, CacheMaxEntries: 100})
}

func checkOnce(t *testing.T, usage Reader) Verdict {
	t.Helper()
	verdict, err := newChecker(usage).Check(context.Background(), orgID)
	if err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}
	return verdict
}

func TestVolumeUnderTheAllowanceIsAdmitted(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded - 1, Found: true,
	}})

	if verdict.Spent {
		t.Error("refused a byte below the allowance; the last included byte is included")
	}
}

func TestVolumePastTheAllowanceIsSpent(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded + 1, Found: true,
	}})

	if !verdict.Spent {
		t.Fatal("admitted a byte past the allowance; the free tier would never end")
	}
}

// The one that would be worst to get wrong: a tier that bills every service it
// sees has no ceiling, so it has no enforced allowance. Blocking a paying
// customer is a far larger failure than the hole this gate closes.
func TestAPlanWithNoCeilingIsNeverSpent(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		Enforced: false, IngestedBytes: 900 * gib, Found: true,
	}})

	if verdict.Spent {
		t.Error("refused an uncapped plan; pro and scale record no max_billed_services")
	}
}

// After the auth gate a missing organization is a broken database, not a caller
// to refuse. Telemetry is the wrong thing to lose over it.
func TestAnOrganizationWithNoRowIsNotRefused(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{Found: false}})

	if verdict.Spent {
		t.Error("refused an organization we could not find")
	}
}

// A plan that includes nothing includes nothing. NULL is what "no allowance
// recorded" is spelled as, and it arrives here as Enforced false.
func TestAZeroAllowanceRefusesOnceAnythingHasArrived(t *testing.T) {
	nothingYet := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: 0, Enforced: true, IngestedBytes: 0, Found: true,
	}})
	if nothingYet.Spent {
		t.Error("refused before anything was ingested; nothing was over anything")
	}

	somethingArrived := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: 0, Enforced: true, IngestedBytes: 1, Found: true,
	}})
	if !somethingArrived.Spent {
		t.Error("a plan recording an allowance of zero admitted volume anyway")
	}
}

func TestTheAllowanceIsReadOncePerCachePeriod(t *testing.T) {
	usage := &fakeUsage{usage: Usage{Found: true}}
	checker := newChecker(usage)

	for range 20 {
		if _, err := checker.Check(context.Background(), orgID); err != nil {
			t.Fatalf("Check() = %v, want no error", err)
		}
	}
	if usage.reads != 1 {
		t.Errorf("read the allowance %d times across 20 exports, want 1: this is the hot path",
			usage.reads)
	}
}

func TestASpentAllowanceIsRereadOnceTheEntryExpires(t *testing.T) {
	usage := &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded + 1, Found: true,
	}}
	checker := newChecker(usage)

	now := time.Now()
	checker.cache.UseClock(func() time.Time { return now })
	if _, err := checker.Check(context.Background(), orgID); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}

	// An upgrade has to open the door again, so the answer is not believed
	// forever.
	now = now.Add(time.Minute + time.Second)
	if _, err := checker.Check(context.Background(), orgID); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}
	if usage.reads != 2 {
		t.Errorf("read the allowance %d times, want 2: a customer who has paid stays refused",
			usage.reads)
	}
}

// The caller decides what to do about an unreadable control plane, so the error
// has to reach it rather than being turned into a verdict here.
func TestAnUnreadableControlPlaneIsReportedRatherThanDecided(t *testing.T) {
	unreachable := errors.New("dial tcp: connection refused")

	_, err := newChecker(&fakeUsage{err: unreachable}).Check(context.Background(), orgID)
	if !errors.Is(err, unreachable) {
		t.Fatalf("Check() = %v, want the lookup failure", err)
	}
}

func TestTheMonthAskedForIsTheCalendarMonthInUTC(t *testing.T) {
	usage := &fakeUsage{usage: Usage{Found: true}}
	checker := newChecker(usage)
	// Mid-afternoon on the last day of a 30-day month, and a month whose length
	// a fixed 31 or 30 would get wrong.
	checker.now = func() time.Time { return time.Date(2026, time.September, 30, 15, 4, 5, 0, time.UTC) }

	if _, err := checker.Check(context.Background(), orgID); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}

	wantFirst := time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC)
	wantLast := time.Date(2026, time.September, 30, 0, 0, 0, 0, time.UTC)
	if !usage.month.FirstDay.Equal(wantFirst) || !usage.month.LastDay.Equal(wantLast) {
		t.Errorf("asked for %v..%v, want %v..%v",
			usage.month.FirstDay, usage.month.LastDay, wantFirst, wantLast)
	}
}

func TestFebruaryEndsWhenFebruaryEnds(t *testing.T) {
	leap := monthOf(time.Date(2028, time.February, 10, 0, 0, 0, 0, time.UTC))
	if got := leap.LastDay.Day(); got != 29 {
		t.Errorf("February 2028 ends on the %d, want the 29th", got)
	}

	plain := monthOf(time.Date(2027, time.February, 10, 0, 0, 0, 0, time.UTC))
	if got := plain.LastDay.Day(); got != 28 {
		t.Errorf("February 2027 ends on the %d, want the 28th", got)
	}
}

// The date in the refusal is the only thing a blocked customer can act on, so
// it is the day after the month, not the last day of it.
func TestTheAllowanceResetsOnTheFirstOfTheNextMonth(t *testing.T) {
	usage := &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded + 1, Found: true,
	}}
	checker := newChecker(usage)
	checker.now = func() time.Time { return time.Date(2026, time.December, 14, 9, 0, 0, 0, time.UTC) }

	verdict, err := checker.Check(context.Background(), orgID)
	if err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}

	want := time.Date(2027, time.January, 1, 0, 0, 0, 0, time.UTC)
	if !verdict.ResetsOn.Equal(want) {
		t.Errorf("ResetsOn = %v, want %v: December rolls the year over", verdict.ResetsOn, want)
	}
}

// The rate applied is the one in force today, not on the month's last day: a
// repricing that starts on the 20th must not govern a month sitting on the 15th
// (ADR 0083).
func TestThePriceIsAskedForAsOfTodayNotTheMonthsEnd(t *testing.T) {
	usage := &fakeUsage{usage: Usage{Found: true}}
	checker := newChecker(usage)
	today := time.Date(2026, time.September, 15, 11, 30, 0, 0, time.UTC)
	checker.now = func() time.Time { return today }

	if _, err := checker.Check(context.Background(), orgID); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}
	if !usage.on.Equal(today) {
		t.Errorf("asked for the price on %v, want %v", usage.on, today)
	}
}

// The free row of migration 0015, read the way the door reads it.
func TestTheSeededFreeTierIncludesSixGibibytes(t *testing.T) {
	perService := 2 * gib
	ceiling := int32(3)

	usage := usageFrom(&perService, &ceiling, 0)

	if usage.IncludedBytes != freeIncluded {
		t.Errorf("included bytes = %d, want %d (2 GiB x 3)", usage.IncludedBytes, freeIncluded)
	}
	if !usage.Enforced {
		t.Error("the free tier's allowance is not enforced; the tier would never end")
	}
}

// The one that would be worst to get wrong, read at the column where it is
// decided: pro and scale record no `max_billed_services`, and NULL is uncapped.
func TestARowWithNoCeilingEnforcesNothing(t *testing.T) {
	perService := 2 * gib

	usage := usageFrom(&perService, nil, 900*gib)

	if usage.Enforced {
		t.Error("a row with no ceiling enforced an allowance; a paying customer would be refused")
	}
	if !usage.Found {
		t.Error("a row that answered was read as no row at all")
	}
}

// The other half of "no allowance recorded": a tier with a billing ceiling but
// no volume figure caps the bill, not the ingest.
func TestARowWithNoVolumeFigureEnforcesNothing(t *testing.T) {
	ceiling := int32(3)

	if usageFrom(nil, &ceiling, 900*gib).Enforced {
		t.Error("a row recording no allowance enforced one anyway")
	}
}

// Zero is a plan that includes nothing, which is a different statement from
// NULL and is taken at its word.
func TestARowRecordingZeroEnforcesZero(t *testing.T) {
	none := int64(0)
	ceiling := int32(3)

	usage := usageFrom(&none, &ceiling, 1)

	if !usage.Enforced || usage.IncludedBytes != 0 {
		t.Errorf("usage = %+v, want an enforced allowance of zero", usage)
	}
	if !usage.spent() {
		t.Error("a plan including nothing admitted volume anyway")
	}
}
