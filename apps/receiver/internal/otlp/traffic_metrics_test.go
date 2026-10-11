package otlp_test

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// The summary line resets every window; a monitor needs totals that only grow.
func TestTrafficTotalsSurviveTheSummaryWindow(t *testing.T) {
	traffic := otlp.NewTraffic()
	traffic.Accepted(otlp.Spans, 5)
	traffic.Refused(otlp.LogRecords, "throttled", 3)
	runSummary(t, traffic)
	traffic.Accepted(otlp.Spans, 2)

	want := `
# HELP nexovue_receiver_items_total Telemetry the doors accepted or refused, in each signal's own unit.
# TYPE nexovue_receiver_items_total counter
nexovue_receiver_items_total{outcome="accepted",signal="data_points"} 0
nexovue_receiver_items_total{outcome="accepted",signal="log_records"} 0
nexovue_receiver_items_total{outcome="accepted",signal="samples"} 0
nexovue_receiver_items_total{outcome="accepted",signal="spans"} 7
nexovue_receiver_items_total{outcome="refused",signal="data_points"} 0
nexovue_receiver_items_total{outcome="refused",signal="log_records"} 3
nexovue_receiver_items_total{outcome="refused",signal="samples"} 0
nexovue_receiver_items_total{outcome="refused",signal="spans"} 0
# HELP nexovue_receiver_requests_total Export requests the doors answered, by outcome.
# TYPE nexovue_receiver_requests_total counter
nexovue_receiver_requests_total{outcome="accepted"} 2
nexovue_receiver_requests_total{outcome="refused"} 1
`
	if err := testutil.CollectAndCompare(traffic, strings.NewReader(want),
		"nexovue_receiver_items_total", "nexovue_receiver_requests_total"); err != nil {
		t.Fatal(err)
	}
	if got := countFor(t, traffic, "throttled"); got != 1 {
		t.Fatalf("throttled events = %v, want 1", got)
	}
}

// Labels come from the fixed lists only, so no caller can grow the series count.
func TestAnUnknownReasonCreatesNoSeries(t *testing.T) {
	traffic := otlp.NewTraffic()
	before := testutil.CollectAndCount(traffic)

	traffic.Refused(otlp.Spans, "a-reason-nobody-listed", 1)

	if after := testutil.CollectAndCount(traffic); after != before {
		t.Fatalf("series went from %d to %d", before, after)
	}
}

func countFor(t *testing.T, collector prometheus.Collector, reason string) float64 {
	t.Helper()
	registry := prometheus.NewPedanticRegistry()
	registry.MustRegister(collector)
	families, err := registry.Gather()
	if err != nil {
		t.Fatal(err)
	}
	for _, family := range families {
		if family.GetName() != "nexovue_receiver_events_total" {
			continue
		}
		for _, metric := range family.GetMetric() {
			for _, label := range metric.GetLabel() {
				if label.GetName() == "reason" && label.GetValue() == reason {
					return metric.GetCounter().GetValue()
				}
			}
		}
	}
	return -1
}
