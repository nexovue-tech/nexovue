package allowance

import (
	"context"
	"errors"
	"testing"
	"time"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

const orgID = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"

// The free tier as migrations 0015 and 0016 seed it: 2 GiB per billed service,
// three billed services, a quarter of it the browser door's. The Go gate and
// `nexovue_api.usage.billing` compute the same figures from that row, and both
// suites use these numbers so a divergence shows up as a failing number rather
// than as a customer told a limit we do not apply.
const (
	gib              = int64(1) << 30
	freeIncluded     = 3 * 2 * gib
	freeBrowserShare = 25
	freeBrowserBytes = freeIncluded / 4
	freeServerBytes  = freeIncluded - freeBrowserBytes
)

// The two doors as the envelope names them.
const (
	serverDoor  = nexovuev1.Door_DOOR_OTLP
	browserDoor = nexovuev1.Door_DOOR_RUM
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

// checkAt asks one door's verdict over a fresh checker.
func checkAt(t *testing.T, usage Reader, door nexovuev1.Door) Verdict {
	t.Helper()
	verdict, err := newChecker(usage).Check(context.Background(), orgID, door)
	if err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}
	return verdict
}

// checkOnce asks at the server door, which is the one every test that is not
// about the split cares about.
func checkOnce(t *testing.T, usage Reader) Verdict {
	t.Helper()
	return checkAt(t, usage, serverDoor)
}

// freeMonth is the seeded free row with a given month metered against it.
func freeMonth(ingested, browserIngested int64) Usage {
	perService, ceiling, share := 2*gib, int32(3), int16(freeBrowserShare)
	return usageFrom(&perService, &ceiling, &share, nil, ingested, browserIngested)
}

func TestVolumeUnderTheAllowanceIsAdmitted(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded - 1, Found: true,
	}})

	if verdict.Refused() {
		t.Error("refused a byte below the allowance; the last included byte is included")
	}
}

func TestVolumePastTheAllowanceIsSpent(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: freeIncluded, Enforced: true,
		IngestedBytes: freeIncluded + 1, Found: true,
	}})

	if !verdict.Refused() {
		t.Fatal("admitted a byte past the allowance; the free tier would never end")
	}
	if verdict.Budget != Pooled {
		t.Errorf("budget = %v, want Pooled: a row with no split has one allowance", verdict.Budget)
	}
}

// A plan with nothing to size an allowance on is never spent. That is now a
// narrower statement than it was: it used to cover every paying tier, because
// nothing sized a paid allowance at all. It now covers a paid tier with no
// active subscription — there is no ceiling to apply, which is not a ceiling
// of zero.
func TestAPlanWithNothingToSizeAnAllowanceOnIsNeverSpent(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{
		Enforced: false, IngestedBytes: 900 * gib, Found: true,
	}})

	if verdict.Refused() {
		t.Error("refused an uncapped plan; pro and scale record no max_billed_services")
	}
}

// After the auth gate a missing organization is a broken database, not a caller
// to refuse. Telemetry is the wrong thing to lose over it.
func TestAnOrganizationWithNoRowIsNotRefused(t *testing.T) {
	verdict := checkOnce(t, &fakeUsage{usage: Usage{Found: false}})

	if verdict.Refused() {
		t.Error("refused an organization we could not find")
	}
}

// A plan that includes nothing includes nothing. NULL is what "no allowance
// recorded" is spelled as, and it arrives here as Enforced false.
func TestAZeroAllowanceRefusesOnceAnythingHasArrived(t *testing.T) {
	nothingYet := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: 0, Enforced: true, IngestedBytes: 0, Found: true,
	}})
	if nothingYet.Refused() {
		t.Error("refused before anything was ingested; nothing was over anything")
	}

	somethingArrived := checkOnce(t, &fakeUsage{usage: Usage{
		IncludedBytes: 0, Enforced: true, IngestedBytes: 1, Found: true,
	}})
	if !somethingArrived.Refused() {
		t.Error("a plan recording an allowance of zero admitted volume anyway")
	}
}

func TestTheAllowanceIsReadOncePerCachePeriod(t *testing.T) {
	usage := &fakeUsage{usage: Usage{Found: true}}
	checker := newChecker(usage)

	for range 20 {
		if _, err := checker.Check(context.Background(), orgID, serverDoor); err != nil {
			t.Fatalf("Check() = %v, want no error", err)
		}
	}
	if usage.reads != 1 {
		t.Errorf("read the allowance %d times across 20 exports, want 1: this is the hot path",
			usage.reads)
	}
}

// Both doors are answered from the one read: the month is the organization's,
// and reading it twice would be two chances for the two to disagree.
func TestBothDoorsShareOneReadOfTheMonth(t *testing.T) {
	usage := &fakeUsage{usage: freeMonth(0, 0)}
	checker := newChecker(usage)

	for _, door := range []nexovuev1.Door{serverDoor, browserDoor, serverDoor, browserDoor} {
		if _, err := checker.Check(context.Background(), orgID, door); err != nil {
			t.Fatalf("Check() = %v, want no error", err)
		}
	}
	if usage.reads != 1 {
		t.Errorf("read the allowance %d times for two doors, want 1", usage.reads)
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
	if _, err := checker.Check(context.Background(), orgID, serverDoor); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}

	// An upgrade has to open the door again, so the answer is not believed
	// forever.
	now = now.Add(time.Minute + time.Second)
	if _, err := checker.Check(context.Background(), orgID, serverDoor); err != nil {
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

	_, err := newChecker(&fakeUsage{err: unreachable}).Check(context.Background(), orgID, serverDoor)
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

	if _, err := checker.Check(context.Background(), orgID, serverDoor); err != nil {
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

	verdict, err := checker.Check(context.Background(), orgID, serverDoor)
	if err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}

	want := time.Date(2027, time.January, 1, 0, 0, 0, 0, time.UTC)
	if !verdict.ResetsOn.Equal(want) {
		t.Errorf("ResetsOn = %v, want %v: December rolls the year over", verdict.ResetsOn, want)
	}
}

// The rate applied is the one in force today, not on the month's last day: a
// repricing that starts on the 20th must not govern a month sitting on the 15th.
func TestThePriceIsAskedForAsOfTodayNotTheMonthsEnd(t *testing.T) {
	usage := &fakeUsage{usage: Usage{Found: true}}
	checker := newChecker(usage)
	today := time.Date(2026, time.September, 15, 11, 30, 0, 0, time.UTC)
	checker.now = func() time.Time { return today }

	if _, err := checker.Check(context.Background(), orgID, serverDoor); err != nil {
		t.Fatalf("Check() = %v, want no error", err)
	}
	if !usage.on.Equal(today) {
		t.Errorf("asked for the price on %v, want %v", usage.on, today)
	}
}

/* ------------------------------------------- the row, read at the column */

// The free row of migration 0015, read the way the door reads it.
func TestTheSeededFreeTierIncludesSixGibibytes(t *testing.T) {
	perService := 2 * gib
	ceiling := int32(3)

	usage := usageFrom(&perService, &ceiling, nil, nil, 0, 0)

	if usage.IncludedBytes != freeIncluded {
		t.Errorf("included bytes = %d, want %d (2 GiB x 3)", usage.IncludedBytes, freeIncluded)
	}
	if !usage.Enforced {
		t.Error("the free tier's allowance is not enforced; the tier would never end")
	}
}

// G6a, read at the column where it is decided. Pro and scale record no
// `max_billed_services`, and until the committed count existed that meant a
// paying customer had **no ceiling at all** while every free one had one.
func TestAPaidRowIsSizedOnTheCustomersCommittedCount(t *testing.T) {
	perService, committed := 2*gib, int32(10)

	usage := usageFrom(&perService, nil, nil, &committed, 900*gib, 0)

	if !usage.Enforced {
		t.Fatal("a paid tier with an active subscription enforced nothing; it has no ceiling at all")
	}
	if want := 20 * gib; usage.IncludedBytes != want {
		t.Errorf("IncludedBytes = %d, want %d: the committed count times the rate",
			usage.IncludedBytes, want)
	}
}

// The other reading of the same row, and the one that keeps a customer who has
// not committed to anything from being refused: no active subscription is
// nothing to size an allowance on, not an allowance of zero.
func TestAPaidRowWithNoActiveSubscriptionEnforcesNothing(t *testing.T) {
	perService := 2 * gib

	usage := usageFrom(&perService, nil, nil, nil, 900*gib, 0)

	if usage.Enforced {
		t.Error("a row with nothing to size an allowance on enforced one anyway")
	}
	if !usage.Found {
		t.Error("a row that answered was read as no row at all")
	}
}

// A plan's own ceiling wins over the committed count. Free records three billed
// services, so a customer who somehow carries a commitment of ten is still held
// to the tier they are on.
func TestThePlansOwnCeilingWinsOverTheCommittedCount(t *testing.T) {
	perService, ceiling, committed := 2*gib, int32(3), int32(10)

	usage := usageFrom(&perService, &ceiling, nil, &committed, 0, 0)

	if usage.IncludedBytes != freeIncluded {
		t.Errorf("IncludedBytes = %d, want %d: the plan's ceiling, not the commitment",
			usage.IncludedBytes, freeIncluded)
	}
}

// A committed customer past their ceiling is refused, which is the whole point
// of the commitment — and the browser share applies to a paid allowance exactly
// as it does to a free one, because it is a share of whatever the allowance
// turned out to be rather than of the free tier's number.
func TestACommittedCustomerPastTheirCeilingIsRefused(t *testing.T) {
	perService, committed, share := 2*gib, int32(10), int16(freeBrowserShare)
	committedIncluded := 20 * gib

	usage := &fakeUsage{
		usage: usageFrom(&perService, nil, &share, &committed, committedIncluded+1, 0),
	}

	if !checkAt(t, usage, serverDoor).Refused() {
		t.Error("admitted a byte past a paid customer's own ceiling; that ceiling is where we stop")
	}

	under := &fakeUsage{usage: usageFrom(&perService, nil, &share, &committed, 1, 1)}
	if checkAt(t, under, browserDoor).Refused() {
		t.Error("refused a paid customer well inside their ceiling")
	}
}

// A share on a row with nothing to size the allowance on changes nothing: there
// is no allowance to split, and the customer stays unrefused at both doors.
func TestAShareOnAnUnsizedRowRefusesNothing(t *testing.T) {
	perService, share := 2*gib, int16(freeBrowserShare)

	usage := &fakeUsage{usage: usageFrom(&perService, nil, &share, nil, 900*gib, 900*gib)}

	if checkAt(t, usage, browserDoor).Refused() || checkAt(t, usage, serverDoor).Refused() {
		t.Error("a plan with no allowance was refused because its row also records a split")
	}
}

// The other half of "no allowance recorded": a tier with a billing ceiling but
// no volume figure caps the bill, not the ingest.
func TestARowWithNoVolumeFigureEnforcesNothing(t *testing.T) {
	ceiling := int32(3)

	if usageFrom(nil, &ceiling, nil, nil, 900*gib, 0).Enforced {
		t.Error("a row recording no allowance enforced one anyway")
	}
}

// Zero is a plan that includes nothing, which is a different statement from
// NULL and is taken at its word.
func TestARowRecordingZeroEnforcesZero(t *testing.T) {
	none := int64(0)
	ceiling := int32(3)

	usage := usageFrom(&none, &ceiling, nil, nil, 1, 0)

	if !usage.Enforced || usage.IncludedBytes != 0 {
		t.Errorf("usage = %+v, want an enforced allowance of zero", usage)
	}
	if !usage.spent(Pooled) {
		t.Error("a plan including nothing admitted volume anyway")
	}
}

/* --------------------------------------- the split between the two doors */

// The free row of migration 0016, read at the column: a quarter of 6 GiB is the
// browser door's, and the rest is the server's. `nexovue_api.usage.billing`
// has to compute the same two numbers from the same row.
func TestTheSeededFreeTierGivesTheBrowserAQuarter(t *testing.T) {
	usage := freeMonth(0, 0)

	if !usage.Split {
		t.Fatal("a row recording a share was read as pooled; the browser could spend the server's month")
	}
	if got := usage.browserIncludedBytes(); got != freeBrowserBytes {
		t.Errorf("browser cap = %d bytes, want %d (25%% of 6 GiB)", got, freeBrowserBytes)
	}
}

// A deleted organization is refused, and this is the test that says the gate
// can tell "deleted" from "missing" at all.
//
// It could not, and that turned the cap off entirely: the statement filtered
// `deleted_at IS NULL`, so a soft-deleted organization returned no row, the
// gate read that as "cannot find" — which it admits, deliberately, because a
// broken control plane must not become an ingest outage — and its keys went on
// authenticating, since the key lookups never join `organizations`. Deleting an
// organization bought unlimited free ingest for whoever still held the key.
func TestADeletedOrganizationIsRefusedRatherThanAdmitted(t *testing.T) {
	deleted := &fakeUsage{usage: Usage{Found: true, Deleted: true}}

	for _, door := range []nexovuev1.Door{serverDoor, browserDoor} {
		verdict := checkAt(t, deleted, door)
		if !verdict.Refused() {
			t.Errorf("%v: a deleted organization was admitted; deletion turns the cap off", door)
		}
		if verdict.Cause != OrganizationGone {
			t.Errorf("%v: verdict = %+v, want OrganizationGone — a spent allowance returns on "+
				"the 1st and a deleted organization never does, so the refusal must not say "+
				"it will", door, verdict)
		}
	}
}

// The case the deleted check must not swallow: an organization we genuinely
// cannot find is still admitted. After the auth gate that means the row went
// away underneath us, which is a broken database rather than a caller to
// refuse — refusing telemetry over it would be the wrong end.
func TestAnOrganizationThatCannotBeFoundIsStillAdmitted(t *testing.T) {
	missing := &fakeUsage{usage: Usage{Found: false}}

	if verdict := checkAt(t, missing, serverDoor); verdict.Refused() {
		t.Errorf("verdict = %+v, want admitted: a control-plane miss must not become an ingest outage", verdict)
	}
}

// A row that records no share is the row every organization had before the
// split existed: one allowance, both doors spend it.
func TestARowRecordingNoShareIsPooledAcrossBothDoors(t *testing.T) {
	perService, ceiling := 2*gib, int32(3)
	usage := &fakeUsage{usage: usageFrom(&perService, &ceiling, nil, nil, freeIncluded+1, freeIncluded+1)}

	for _, door := range []nexovuev1.Door{serverDoor, browserDoor} {
		verdict := checkAt(t, usage, door)
		if !verdict.Refused() || verdict.Budget != Pooled {
			t.Errorf("%v: verdict = %+v, want the pooled allowance spent", door, verdict)
		}
	}
}

// The property the split exists for: a public key is printed in the page, so
// anyone can spend the browser's share — and however far they spend it, the
// server door still answers. The DoS the pooled allowance opened is closed here
// or nowhere.
func TestASpentBrowserShareDoesNotSpendTheServers(t *testing.T) {
	cases := []struct {
		name            string
		browserIngested int64
	}{
		{"one byte over the browser's share", freeBrowserBytes + 1},
		{"the whole plan total through the browser door", freeIncluded + 1},
		{"a scraped key spending fifty months in a minute", 50 * freeIncluded},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			usage := &fakeUsage{usage: freeMonth(tc.browserIngested, tc.browserIngested)}

			browser := checkAt(t, usage, browserDoor)
			if !browser.Refused() || browser.Budget != Browser {
				t.Errorf("browser door verdict = %+v, want the browser share spent", browser)
			}
			if server := checkAt(t, usage, serverDoor); server.Refused() {
				t.Errorf("the server door was refused at %d browser bytes; the backend must keep "+
					"reporting whatever happens to the website", tc.browserIngested)
			}
		})
	}
}

// **The share is a cap, not a reservation, and this is the test that says so.**
// An organization sending nothing through the browser door reaches the plan's
// whole allowance at the server door. Under the partition this test replaces it
// was refused at 4.5 GiB with 1.5 GiB it could not reach and nothing able to
// reach it — the browser SDK is P1-39 and unbuilt.
func TestABrowserShareNobodyUsesIsNotStrandedFromTheServer(t *testing.T) {
	justUnder := &fakeUsage{usage: freeMonth(freeIncluded, 0)}
	if verdict := checkAt(t, justUnder, serverDoor); verdict.Refused() {
		t.Errorf("server door refused at %d of the plan's %d bytes, with no browser byte spent: "+
			"the browser's share was reserved rather than capped", freeIncluded, freeIncluded)
	}

	over := &fakeUsage{usage: freeMonth(freeIncluded+1, 0)}
	if verdict := checkAt(t, over, serverDoor); !verdict.Refused() {
		t.Error("server door admitted past the plan's whole allowance")
	}
}

// The other half of the same rule: a cap that bound only its own door would let
// an organization spend the whole plan on the server door and then a further
// share on the browser door — 7.5 GiB of a 6 GiB plan. Every door is judged
// against the whole as well, so the sum stays at the plan's total.
func TestTheBrowserDoorCannotSpendPastAPlanTheServerAlreadyFilled(t *testing.T) {
	spentOnTheServer := &fakeUsage{usage: freeMonth(freeIncluded+1, 0)}

	verdict := checkAt(t, spentOnTheServer, browserDoor)
	if !verdict.Refused() {
		t.Error("the browser door was admitted on a plan the server door had already spent; " +
			"the two now sum past the month, which the plan's total refuses")
	}
}

// The last byte of each share is included, at both doors.
func TestEachShareIsSpentOnlyPastItsOwnLastByte(t *testing.T) {
	atTheLine := &fakeUsage{usage: freeMonth(freeIncluded, freeBrowserBytes)}

	if checkAt(t, atTheLine, browserDoor).Refused() {
		t.Error("refused the browser door on its last included byte")
	}
	if checkAt(t, atTheLine, serverDoor).Refused() {
		t.Error("refused the server door on its last included byte")
	}
}

// Rows metered before the door was recorded carry no browser bytes. They still
// count — against the whole allowance, which every door is judged on — so
// history is never read as nobody's. What they cannot do is fill the browser's
// cap, because the browser door is judged on browser bytes alone.
func TestBytesMeteredBeforeTheDoorWasRecordedStillCountAgainstThePlan(t *testing.T) {
	usage := freeMonth(freeIncluded+1, 0)

	if !usage.spent(Pooled) {
		t.Error("history was read as nobody's, and the plan's allowance never ends")
	}

	underTheWhole := freeMonth(freeBrowserBytes, 0)
	if underTheWhole.spent(Browser) {
		t.Error("unattributed history filled the browser's cap, which counts browser bytes only")
	}
}

// Only the browser door carries the cap. An entrance that stamped nothing, and
// the scheduler's own doors, are judged against the plan's whole allowance —
// the cap exists to bound a key printed in a web page, and none of these is
// that.
func TestEveryDoorButTheBrowsersIsJudgedOnTheWholeAllowance(t *testing.T) {
	usage := &fakeUsage{usage: freeMonth(freeIncluded+1, 0)}

	for _, door := range []nexovuev1.Door{
		nexovuev1.Door_DOOR_OTLP,
		nexovuev1.Door_DOOR_SYNTHETIC,
		nexovuev1.Door_DOOR_POLLER,
		nexovuev1.Door_DOOR_UNSPECIFIED,
	} {
		if verdict := checkAt(t, usage, door); !verdict.Refused() || verdict.Budget != Pooled {
			t.Errorf("%v: verdict = %+v, want the plan's whole allowance spent", door, verdict)
		}
	}
}

// Whatever the share, the two budgets are the plan's total and nothing else —
// rounding down on the browser's side, so the remainder is the server's. This
// is what keeps a split from being a way to buy more month.
// Whatever the share, no organization can be admitted past the plan's total at
// any door. Checked across the whole range rather than at the seeded 25%,
// because the column takes any percentage and the guarantee must not depend on
// which one a price row happens to carry.
func TestNoShareLetsAnyDoorSpendPastThePlanTotal(t *testing.T) {
	perService, ceiling := 2*gib, int32(3)
	for _, share := range []int16{0, 1, 25, 33, 50, 99, 100} {
		cap := usageFrom(&perService, &ceiling, &share, nil, 0, 0).browserIncludedBytes()
		if cap < 0 || cap > freeIncluded {
			t.Errorf("share %d%%: browser cap of %d is outside the plan's %d", share, cap, freeIncluded)
		}

		// One byte past the whole, spent entirely at whichever door the share
		// does not cover. Both doors must refuse: the browser's own cap is
		// untouched here, so only the whole-allowance test can catch it.
		over := usageFrom(&perService, &ceiling, &share, nil, freeIncluded+1, 0)
		if !over.spent(Pooled) {
			t.Errorf("share %d%%: server door admitted past the plan's total", share)
		}
		if !over.spent(Browser) {
			t.Errorf("share %d%%: browser door admitted past the plan's total with its own cap unspent — "+
				"the two now sum past the month", share)
		}
	}
}

// A share of zero is a plan that lets the browser door send nothing, and one
// hundred is a plan that lets the server door send nothing. Both are honoured
// at their word, the way a zero allowance is.
func TestAShareOfZeroOrOneHundredIsHonoured(t *testing.T) {
	perService, ceiling := 2*gib, int32(3)

	none := int16(0)
	nothingForTheBrowser := &fakeUsage{usage: usageFrom(&perService, &ceiling, &none, nil, 1, 1)}
	if !checkAt(t, nothingForTheBrowser, browserDoor).Refused() {
		t.Error("a browser share of zero admitted a browser byte")
	}
	if checkAt(t, nothingForTheBrowser, serverDoor).Refused() {
		t.Error("a browser share of zero refused the server door, which has the whole plan")
	}

	// A hundred percent is a cap of the whole plan, not a reservation of it. The
	// browser door may spend everything; the server door may spend whatever the
	// browser has not. Under the partition this meant the server got nothing,
	// which is the reading this change replaces.
	all := int16(100)
	unspent := &fakeUsage{usage: usageFrom(&perService, &ceiling, &all, nil, 1, 0)}
	if checkAt(t, unspent, serverDoor).Refused() {
		t.Error("a browser share of one hundred refused a server byte the browser had not spent")
	}
	if checkAt(t, unspent, browserDoor).Refused() {
		t.Error("a browser share of one hundred refused the browser door, which may spend the whole plan")
	}

	// The browser has spent the plan's whole allowance and the server one byte
	// beyond it. Charged is the cap plus that byte, so the server door closes —
	// a hundred percent to the browser leaves the server nothing once the
	// browser has actually taken it.
	spentByTheBrowser := &fakeUsage{
		usage: usageFrom(&perService, &ceiling, &all, nil, freeIncluded+1, freeIncluded),
	}
	if !checkAt(t, spentByTheBrowser, serverDoor).Refused() {
		t.Error("the server door was admitted after the browser spent the whole plan")
	}
}
