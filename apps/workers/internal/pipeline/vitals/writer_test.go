package vitals

import (
	"testing"
	"time"
)

// fixedMinute is the minute rollup_test.go already builds its readings in.
func fixedMinute() time.Time {
	return time.Date(2026, 9, 6, 11, 30, 0, 0, time.UTC)
}

// The token is what stops a retried batch double-counting, and what a genuinely
// new batch must not collide with. Every column of the rollup's sort key has to
// be in it: the minute a customer first configures a route, that minute's rows
// differ from the one before in nothing else, and a token that ignored `route`
// would have the store drop them as a repeat — data lost, silently.
func TestTheRollupTokenChangesWhenOnlyTheRouteDoes(t *testing.T) {
	unrouted := Stat{
		Minute: fixedMinute(), TenantID: "tenant-a", ProjectID: "project-a",
		DerivedRetentionDays: 395, VitalName: "lcp", URLPath: "/checkout",
		DeviceClass: DeviceClassMobile, ReadingCount: 1, Values: []float64{1000},
	}
	routed := unrouted
	routed.Route = "/checkout/{orderId}"

	if rollupToken([]Stat{unrouted}) == rollupToken([]Stat{routed}) {
		t.Error("the same token for a routed and an unrouted batch: the store would drop one")
	}
}

func TestTheReadingTokenChangesWhenOnlyTheRouteDoes(t *testing.T) {
	unrouted := Reading{
		ReceivedAt: fixedMinute(), TenantID: "tenant-a", ProjectID: "project-a",
		SessionID: "s", VitalName: "lcp", VitalID: "a", Value: 1000,
		URLPath: "/checkout", DeviceClass: DeviceClassMobile,
	}
	routed := unrouted
	routed.Route = "/checkout/{orderId}"

	if readingToken([]Reading{unrouted}) == readingToken([]Reading{routed}) {
		t.Error("the same token for a routed and an unrouted batch: the store would drop one")
	}
}

// The same batch twice must still be one token, or a retry double-counts.
//
// The two Stats are built separately rather than reusing one value. Hashing the
// same variable twice would pass on a token derived from the value's address
// and prove nothing about the fields — and `f(x) != f(x)` is a tautology the
// linter is right to refuse.
func TestTheSameRowsProduceTheSameToken(t *testing.T) {
	first := Stat{
		Minute: fixedMinute(), TenantID: "tenant-a", ProjectID: "project-a",
		DerivedRetentionDays: 395, VitalName: "lcp", Route: "/checkout/{orderId}",
		URLPath: "/checkout/1", DeviceClass: DeviceClassMobile,
		ReadingCount: 1, Values: []float64{1000},
	}
	second := Stat{
		Minute: fixedMinute(), TenantID: "tenant-a", ProjectID: "project-a",
		DerivedRetentionDays: 395, VitalName: "lcp", Route: "/checkout/{orderId}",
		URLPath: "/checkout/1", DeviceClass: DeviceClassMobile,
		ReadingCount: 1, Values: []float64{1000},
	}
	if rollupToken([]Stat{first}) != rollupToken([]Stat{second}) {
		t.Error("two batches of identical rows produced two tokens, so a retry would double-count")
	}
}
