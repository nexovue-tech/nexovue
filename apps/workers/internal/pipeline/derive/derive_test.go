package derive_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

var base = time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

func span(at time.Time, service, endpoint string, durationMS float64, errored bool) derive.Span {
	return derive.Span{
		TenantID:    "tenant-1",
		ProjectID:   "project-1",
		ServiceName: service,
		Endpoint:    endpoint,
		HTTPMethod:  "GET",
		Timestamp:   at,
		DurationMS:  durationMS,
		Errored:     errored,
	}
}

// valuesByMetric totals every point per metric name, which is how a dashboard
// reads the table.
func valuesByMetric(points []derive.Point) map[string]float64 {
	totals := make(map[string]float64)
	for _, point := range points {
		totals[point.MetricName] += point.Value
	}
	return totals
}

func TestCountersTotalEverySpanTheySee(t *testing.T) {
	counters := derive.NewCounters(0)
	for range 7 {
		counters.Observe(span(base, "checkout", "/checkout", 10, false))
	}
	for range 3 {
		counters.Observe(span(base, "checkout", "/checkout", 100, true))
	}

	totals := valuesByMetric(counters.Drain(base.Add(2 * time.Minute)))
	want := map[string]float64{
		derive.MetricSpanCount:       10,
		derive.MetricSpanErrors:      3,
		derive.MetricSpanDurationSum: 7*10 + 3*100,
	}
	for name, wanted := range want {
		if totals[name] != wanted {
			t.Errorf("%s = %v, want %v", name, totals[name], wanted)
		}
	}
}

func TestCountersSplitBySeries(t *testing.T) {
	counters := derive.NewCounters(0)
	counters.Observe(span(base, "checkout", "/checkout", 1, false))
	counters.Observe(span(base, "payments", "/charge", 1, false))
	counters.Observe(span(base.Add(time.Minute), "checkout", "/checkout", 1, false))

	points := counters.Drain(base.Add(3 * time.Minute))
	counts := 0
	for _, point := range points {
		if point.MetricName == derive.MetricSpanCount {
			counts++
		}
	}
	if counts != 3 {
		t.Errorf("got %d count rows, want one per service, endpoint and minute", counts)
	}
}

func TestEachRowCarriesTheDimensionsThatAreNotColumns(t *testing.T) {
	counters := derive.NewCounters(0)
	counters.Observe(span(base, "checkout", "/checkout", 1, false))

	points := counters.Drain(base.Add(2 * time.Minute))
	if len(points) == 0 {
		t.Fatal("Drain() returned nothing")
	}
	point := points[0]

	if point.Timestamp != base {
		t.Errorf("Timestamp = %s, want the start of the minute (%s)", point.Timestamp, base)
	}
	if point.ServiceName != "checkout" {
		t.Errorf("ServiceName = %q, want checkout", point.ServiceName)
	}
	var attrs map[string]string
	if err := json.Unmarshal([]byte(point.Attrs), &attrs); err != nil {
		t.Fatalf("Attrs is not JSON: %v (%q)", err, point.Attrs)
	}
	if attrs["endpoint"] != "/checkout" || attrs["http_method"] != "GET" {
		t.Errorf("Attrs = %q, want the endpoint and method", point.Attrs)
	}
}

// A minute still open must not be written: writing it would be a partial total
// that nothing later corrects.
func TestAnOpenMinuteIsNotDrained(t *testing.T) {
	counters := derive.NewCounters(10 * time.Second)
	counters.Observe(span(base, "checkout", "/checkout", 1, false))

	if points := counters.Drain(base.Add(65 * time.Second)); len(points) != 0 {
		t.Errorf("Drain() returned %d points before the grace period, want none", len(points))
	}
	if points := counters.Drain(base.Add(71 * time.Second)); len(points) == 0 {
		t.Error("Drain() returned nothing once the minute and its grace had passed")
	}
}

// A span that arrives after its minute was written reopens it. The table is
// summed on read, so the second row costs a row and not accuracy.
func TestALateSpanReopensItsMinuteRatherThanBeingLost(t *testing.T) {
	counters := derive.NewCounters(0)
	counters.Observe(span(base, "checkout", "/checkout", 1, false))
	first := valuesByMetric(counters.Drain(base.Add(2 * time.Minute)))

	counters.Observe(span(base, "checkout", "/checkout", 1, false))
	second := valuesByMetric(counters.Drain(base.Add(3 * time.Minute)))

	if total := first[derive.MetricSpanCount] + second[derive.MetricSpanCount]; total != 2 {
		t.Errorf("the two flushes total %v spans, want 2", total)
	}
}

// Shutdown must write the open minute too: an unwritten counter is a hole in a
// graph that nothing will ever fill.
func TestDrainAllWritesTheOpenMinute(t *testing.T) {
	counters := derive.NewCounters(time.Hour)
	counters.Observe(span(base, "checkout", "/checkout", 1, false))

	if totals := valuesByMetric(counters.DrainAll()); totals[derive.MetricSpanCount] != 1 {
		t.Errorf("DrainAll() counted %v spans, want 1", totals[derive.MetricSpanCount])
	}
	if counters.Pending() != 0 {
		t.Errorf("Pending() = %d after DrainAll(), want 0", counters.Pending())
	}
}

// Offsets may only be committed once the counters they fed are stored, so
// Drained has to agree with Drain about when that is.
func TestDrainedAgreesWithDrain(t *testing.T) {
	counters := derive.NewCounters(10 * time.Second)
	minute := counters.Observe(span(base, "checkout", "/checkout", 1, false))

	if counters.Drained(base.Add(65*time.Second), minute) {
		t.Error("Drained() said yes while Drain() still holds the minute")
	}
	if !counters.Drained(base.Add(71*time.Second), minute) {
		t.Error("Drained() said no after the minute and its grace had passed")
	}
	if !counters.Drained(base, time.Time{}) {
		t.Error("Drained() said no for a batch that counted nothing")
	}
}
