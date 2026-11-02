package derive

import "testing"

func serviceRows() []ServiceStat {
	first := ServiceStat{
		Minute: serviceMinute, TenantID: "tenant-1", ProjectID: "project-1", RetentionDays: 30,
		ServiceName: "checkout", RequestCount: 12, ErrorCount: 1,
	}
	first.LatencyBuckets[LatencyBucket(5)] = 11
	first.LatencyBuckets[LatencyBucket(900)] = 1
	second := first
	second.ServiceName = "payments"
	return []ServiceStat{first, second}
}

// The token is what lets the store drop a retried insert after a lost
// acknowledgement: the same rows must carry the same token, or a summing
// rollup takes the retry twice.
func TestTheSameServiceRowsCarryTheSameToken(t *testing.T) {
	first, again := serviceRows(), serviceRows()
	if serviceDeduplicationToken(first) != serviceDeduplicationToken(again) {
		t.Error("the same rows produced two tokens; a retry would be summed in twice")
	}
}

// And different rows must not share one, or a late reopening of a minute would
// be dropped as a duplicate. One request moved between buckets is enough.
func TestServiceRowsThatDifferCarryDifferentTokens(t *testing.T) {
	base := serviceDeduplicationToken(serviceRows())

	moved := serviceRows()
	moved[1].LatencyBuckets[LatencyBucket(5)]--
	moved[1].LatencyBuckets[LatencyBucket(5000)]++
	if serviceDeduplicationToken(moved) == base {
		t.Error("a request moved between buckets kept the token; the new rows would be dropped")
	}

	reordered := serviceRows()
	reordered[0], reordered[1] = reordered[1], reordered[0]
	if serviceDeduplicationToken(reordered) == base {
		t.Error("the rows in another order kept the token; the token must follow the block")
	}
}
