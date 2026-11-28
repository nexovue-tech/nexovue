package derive

import (
	"testing"
	"time"
)

func overCapEntries(services uint64) []ServiceCap {
	return []ServiceCap{
		{Minute: serviceMinute, TenantID: "tenant-a", RetentionDays: 30, Services: services},
		{Minute: serviceMinute.Add(time.Minute), TenantID: "tenant-b", RetentionDays: 90, Services: 1},
	}
}

// The table sums, so the same entries must carry the same token, or a retry
// after a lost acknowledgement doubles a minute's count.
func TestTheSameOverCapEntriesCarryTheSameToken(t *testing.T) {
	first, again := overCapEntries(3), overCapEntries(3)
	if overCapDeduplicationToken(first) != overCapDeduplicationToken(again) {
		t.Error("the same entries produced two tokens; a retry would be summed in twice")
	}
}

// A different count is a different drain, such as a minute reopened by late
// spans; sharing a token would drop it as a repeat.
func TestOverCapEntriesWithADifferentCountCarryADifferentToken(t *testing.T) {
	if overCapDeduplicationToken(overCapEntries(3)) == overCapDeduplicationToken(overCapEntries(4)) {
		t.Error("two different drains shared a token; the second would be dropped as a repeat")
	}
}
