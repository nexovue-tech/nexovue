package derive_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/latencysample"
)

// endpointSpan is a span as the rollup sees it: a kind, a name, and an identity
// that weights its duration in the latency sample.
func endpointSpan(at time.Time, kind, name string, durationMS float64, errored bool) derive.Span {
	return derive.Span{
		TenantID:      "tenant-1",
		ProjectID:     "project-1",
		RetentionDays: 395,
		ServiceName:   "checkout",
		Endpoint:      name,
		SpanKind:      kind,
		SpanName:      name,
		TraceID:       []byte("trace"),
		SpanID:        []byte(fmt.Sprintf("%s-%s-%v-%v", kind, name, durationMS, errored)),
		Timestamp:     at,
		ReceivedAt:    at,
		DurationMS:    durationMS,
		Errored:       errored,
	}
}

func onlyEndpointRow(t *testing.T, drained derive.Derived) derive.EndpointStat {
	t.Helper()
	if len(drained.Endpoints) != 1 {
		t.Fatalf("drained %d endpoint rows, want exactly 1", len(drained.Endpoints))
	}
	return drained.Endpoints[0]
}

// The rollup answers "how is each service doing": a CLIENT span is this
// service's call out to someone else, and is already the callee's own SERVER
// span. Counting it here would count one request twice.
func TestOnlyInboundSpansReachTheEndpointRollup(t *testing.T) {
	counters := derive.NewCounters(0)
	for _, kind := range []string{"SERVER", "CONSUMER", "CLIENT", "PRODUCER", "INTERNAL", ""} {
		counters.Observe(now, endpointSpan(base, kind, "/checkout", 5, false))
	}

	drained := counters.Drain(base.Add(2 * time.Minute))
	kinds := make(map[string]uint64)
	for _, row := range drained.Endpoints {
		kinds[row.SpanKind] = row.RequestCount
	}
	if len(kinds) != 2 || kinds["SERVER"] != 1 || kinds["CONSUMER"] != 1 {
		t.Errorf("rollup rows by kind = %v, want one SERVER row and one CONSUMER row", kinds)
	}

	// The counters beside it still see every span, whatever its kind.
	if counted := valuesByMetric(drained)[derive.MetricSpanCount]; counted != 6 {
		t.Errorf("%s = %v, want 6: the counters count every kind", derive.MetricSpanCount, counted)
	}
}

func TestAnEndpointRowCarriesItsCountsAndTheDurationsBehindThem(t *testing.T) {
	counters := derive.NewCounters(0)
	for _, ms := range []float64{10, 20, 30, 40} {
		counters.Observe(now, endpointSpan(base, "SERVER", "GET /pay", ms, false))
	}
	counters.Observe(now, endpointSpan(base, "SERVER", "GET /pay", 50, true))

	row := onlyEndpointRow(t, counters.Drain(base.Add(2*time.Minute)))
	if row.Minute != base {
		t.Errorf("Minute = %s, want the start of the minute (%s)", row.Minute, base)
	}
	if row.TenantID != "tenant-1" || row.ProjectID != "project-1" {
		t.Errorf("tenancy = %s/%s, want tenant-1/project-1", row.TenantID, row.ProjectID)
	}
	if row.RetentionDays != 395 {
		t.Errorf("RetentionDays = %d, want 395: the row must land in its own partition", row.RetentionDays)
	}
	if row.ServiceName != "checkout" || row.SpanKind != "SERVER" || row.SpanName != "GET /pay" {
		t.Errorf("row identity = %s/%s/%s, want checkout/SERVER/GET /pay",
			row.ServiceName, row.SpanKind, row.SpanName)
	}
	if row.RequestCount != 5 || row.ErrorCount != 1 {
		t.Errorf("counts = %d requests and %d errors, want 5 and 1", row.RequestCount, row.ErrorCount)
	}

	want := []float64{10, 20, 30, 40, 50}
	if len(row.LatencyMS) != len(want) {
		t.Fatalf("LatencyMS = %v, want every duration (%v)", row.LatencyMS, want)
	}
	for i, ms := range want {
		if row.LatencyMS[i] != ms {
			t.Fatalf("LatencyMS = %v, want %v sorted ascending", row.LatencyMS, want)
		}
	}
}

// The whole point of the rollup being here: it is the same span the counters
// saw, in the same minute, so a screen reading one and an alert reading the
// other cannot disagree.
func TestTheRollupAndTheCountersAgreeOnTheSameSpans(t *testing.T) {
	const inbound = 40
	const failed = 7

	counters := derive.NewCounters(0)
	for index := range inbound {
		counters.Observe(now, endpointSpan(base, "SERVER", "GET /pay", float64(index), index < failed))
	}

	drained := counters.Drain(base.Add(2 * time.Minute))
	row := onlyEndpointRow(t, drained)
	counted := valuesByMetric(drained)

	if float64(row.RequestCount) != counted[derive.MetricSpanCount] {
		t.Errorf("rollup counted %d requests, the counters %v", row.RequestCount, counted[derive.MetricSpanCount])
	}
	if float64(row.ErrorCount) != counted[derive.MetricSpanErrors] {
		t.Errorf("rollup counted %d errors, the counters %v", row.ErrorCount, counted[derive.MetricSpanErrors])
	}
	if row.RequestCount != inbound || row.ErrorCount != failed {
		t.Errorf("counts = %d/%d, want %d/%d", row.RequestCount, row.ErrorCount, inbound, failed)
	}
}

// An endpoint is (service, kind, name): a queue handler and an HTTP route can
// share a name and are not the same endpoint.
func TestTheRollupSplitsByServiceKindNameAndMinute(t *testing.T) {
	counters := derive.NewCounters(0)
	counters.Observe(now, endpointSpan(base, "SERVER", "/pay", 1, false))
	counters.Observe(now, endpointSpan(base, "CONSUMER", "/pay", 1, false))
	counters.Observe(now, endpointSpan(base, "SERVER", "/refund", 1, false))
	counters.Observe(now, endpointSpan(base.Add(time.Minute), "SERVER", "/pay", 1, false))

	other := endpointSpan(base, "SERVER", "/pay", 1, false)
	other.ServiceName = "payments"
	counters.Observe(now, other)

	if rows := counters.Drain(base.Add(3 * time.Minute)).Endpoints; len(rows) != 5 {
		t.Errorf("drained %d rows, want one per service, kind, name and minute (5)", len(rows))
	}
}

// A minute still open must not be written, and Drained has to agree — offsets
// commit only once everything derived for a minute has reached the store, and
// one answer covers both outputs.
func TestTheRollupSettlesWithTheCounters(t *testing.T) {
	counters := derive.NewCounters(10 * time.Second)
	minute, _ := counters.Observe(now, endpointSpan(base, "SERVER", "/pay", 1, false))

	if rows := counters.Drain(base.Add(65 * time.Second)).Endpoints; len(rows) != 0 {
		t.Errorf("drained %d rows before the grace period, want none", len(rows))
	}
	if counters.Drained(base.Add(65*time.Second), minute) {
		t.Error("Drained() said yes while the rollup row was still held")
	}
	if counters.PendingEndpoints() != 1 {
		t.Errorf("PendingEndpoints() = %d, want 1", counters.PendingEndpoints())
	}

	if rows := counters.Drain(base.Add(71 * time.Second)).Endpoints; len(rows) != 1 {
		t.Errorf("drained %d rows after the minute and its grace, want 1", len(rows))
	}
	if counters.PendingEndpoints() != 0 {
		t.Errorf("PendingEndpoints() = %d after the drain, want 0", counters.PendingEndpoints())
	}
}

// Shutdown must write the open minute too: an unwritten rollup row is a gap in
// the service overview that nothing will ever fill.
func TestDrainAllWritesTheOpenEndpointMinute(t *testing.T) {
	counters := derive.NewCounters(time.Hour)
	counters.Observe(now, endpointSpan(base, "SERVER", "/pay", 1, false))

	if rows := counters.DrainAll().Endpoints; len(rows) != 1 {
		t.Errorf("DrainAll() returned %d rows, want the open minute", len(rows))
	}
	if counters.PendingEndpoints() != 0 {
		t.Errorf("PendingEndpoints() = %d after DrainAll(), want 0", counters.PendingEndpoints())
	}
}

// A span whose own clock is not trusted is counted at receive time — and the
// rollup row has to land in that same minute, or a screen and an alert would
// put the same request in two different minutes.
func TestAnUntrustedTimestampBucketsTheRollupAtReceiveTime(t *testing.T) {
	receivedAt := base.Add(30 * time.Second)
	span := endpointSpan(receivedAt.AddDate(1, 0, 0), "SERVER", "/pay", 1, false)
	span.ReceivedAt = receivedAt

	counters := derive.NewCounters(0)
	minute, discarded := counters.Observe(receivedAt, span)
	if discarded == 0 {
		t.Fatal("a timestamp a year ahead was trusted; the fixture proves nothing")
	}

	row := onlyEndpointRow(t, counters.DrainAll())
	if row.Minute != base {
		t.Errorf("Minute = %s, want the minute we received it (%s)", row.Minute, base)
	}
	if row.Minute != minute {
		t.Errorf("the rollup row is in %s but Observe reported %s; offsets would commit early",
			row.Minute, minute)
	}
}

// Above the sample cap the row carries what ClickHouse's own `quantiles` would
// have kept anyway — the reservoir is the same size — so nothing is thinned
// twice and the state stays a state.
func TestTheLatencySampleIsCappedAtTheStoresOwnReservoir(t *testing.T) {
	counters := derive.NewCounters(0)
	for index := range latencysample.MaxSamples * 2 {
		span := endpointSpan(base, "SERVER", "/pay", float64(index), false)
		span.SpanID = []byte(fmt.Sprintf("span-%d", index))
		counters.Observe(now, span)
	}

	row := onlyEndpointRow(t, counters.DrainAll())
	if row.RequestCount != latencysample.MaxSamples*2 {
		t.Errorf("RequestCount = %d, want every span counted (%d): the count is exact even "+
			"when the sample is not", row.RequestCount, latencysample.MaxSamples*2)
	}
	if len(row.LatencyMS) != latencysample.MaxSamples {
		t.Errorf("LatencyMS holds %d durations, want the cap (%d)",
			len(row.LatencyMS), latencysample.MaxSamples)
	}
}
