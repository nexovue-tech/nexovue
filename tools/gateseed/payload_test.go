package main

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"
)

// settings is the default configuration, which is what the gate runs with.
func settings(t *testing.T) Config {
	t.Helper()
	cfg, err := Load()
	if err != nil {
		t.Fatalf("loading the defaults: %v", err)
	}
	return cfg
}

// spansOf digs the spans out of an OTLP/JSON traces body, through the same
// shape a receiver walks, so a change to the nesting fails here.
func spansOf(t *testing.T, body map[string]any) []map[string]any {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("encoding: %v", err)
	}
	var walked struct {
		ResourceSpans []struct {
			Resource struct {
				Attributes []struct {
					Key   string `json:"key"`
					Value struct {
						StringValue string `json:"stringValue"`
					} `json:"value"`
				} `json:"attributes"`
			} `json:"resource"`
			ScopeSpans []struct {
				Spans []map[string]any `json:"spans"`
			} `json:"scopeSpans"`
		} `json:"resourceSpans"`
	}
	if err := json.Unmarshal(encoded, &walked); err != nil {
		t.Fatalf("decoding: %v", err)
	}
	if len(walked.ResourceSpans) != 1 || len(walked.ResourceSpans[0].ScopeSpans) != 1 {
		t.Fatalf("want one resource and one scope, got %+v", walked)
	}
	return walked.ResourceSpans[0].ScopeSpans[0].Spans
}

func TestTracesSendsOneTracePerSpanAndExactlyOneException(t *testing.T) {
	cfg := settings(t)
	spans := spansOf(t, Traces(cfg, 12, time.Now()))

	if len(spans) != 12 {
		t.Fatalf("want 12 spans, got %d", len(spans))
	}

	seen, exceptions := map[string]bool{}, 0
	for _, span := range spans {
		id, _ := span["traceId"].(string)
		if id == "" {
			t.Fatal("a span with no trace id")
		}
		if seen[id] {
			t.Fatalf("trace id %s used twice: the isolation suite pages traces, not spans", id)
		}
		seen[id] = true
		if _, has := span["events"]; has {
			exceptions++
		}
	}
	// One, because the error-group reads ask whether any exception exists — and
	// because every seeded trace being a failure would put a fault nobody has on
	// the Errors screen.
	if exceptions != 1 {
		t.Fatalf("want exactly one span carrying an exception event, got %d", exceptions)
	}
}

func TestTracesAreSlowEnoughToSurviveTailSampling(t *testing.T) {
	cfg := settings(t)
	// The shipped policy keeps every trace slower than this. Measured
	// 2026-09-21: 80 fast healthy traces became 11 rows.
	const shippedSlowThreshold = 2 * time.Second

	for _, span := range spansOf(t, Traces(cfg, 5, time.Now())) {
		start, end := nanosOf(t, span, "startTimeUnixNano"), nanosOf(t, span, "endTimeUnixNano")
		if took := end.Sub(start); took <= shippedSlowThreshold {
			t.Fatalf(
				"a seeded trace took %s, at or under the %s the sampler keeps above. "+
					"Nine in ten would be dropped and the gate would seed nothing.",
				took, shippedSlowThreshold,
			)
		}
	}
}

func nanosOf(t *testing.T, span map[string]any, key string) time.Time {
	t.Helper()
	raw, ok := span[key].(string)
	if !ok {
		t.Fatalf("span has no %s", key)
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		t.Fatalf("parsing %s %q: %v", key, raw, err)
	}
	return time.Unix(0, value)
}

func TestMetricsIsACumulativeHistogramTheReadCanFold(t *testing.T) {
	cfg := settings(t)
	points := pointsOf(t, Metrics(cfg, time.Now()))

	if want := cfg.Series * cfg.Exports; len(points) != want {
		t.Fatalf("want %d points, got %d", want, len(points))
	}
	for _, point := range points {
		if len(point.ExplicitBounds) != len(BOUNDS) {
			t.Fatalf("want %d bounds, got %d", len(BOUNDS), len(point.ExplicitBounds))
		}
		// The invariant the read rests on: one more bucket than bounds, because
		// the last one is unbounded.
		if len(point.BucketCounts) != len(point.ExplicitBounds)+1 {
			t.Fatalf(
				"%d buckets against %d bounds: a histogram has exactly one more",
				len(point.BucketCounts), len(point.ExplicitBounds),
			)
		}
		var summed uint64
		for _, count := range point.BucketCounts {
			summed += count
		}
		if summed != point.Count {
			t.Fatalf("count says %d, the buckets hold %d", point.Count, summed)
		}
	}
}

func TestMetricsCountsRiseWithinASeries(t *testing.T) {
	cfg := settings(t)
	points := pointsOf(t, Metrics(cfg, time.Now()))

	// **The whole point of sending several.** `metrics_live` proves the read does
	// not sum cumulative points; a single point per series, or a flat one, makes
	// the naive sum and the window's delta the same number and the test would
	// pass on a shape that cannot exhibit the defect.
	for series := range cfg.Series {
		previous := uint64(0)
		for export := range cfg.Exports {
			point := points[series*cfg.Exports+export]
			if point.Count <= previous {
				t.Fatalf(
					"series %d export %d reports %d, not above the previous %d — "+
						"cumulative counts only rise",
					series, export, point.Count, previous,
				)
			}
			previous = point.Count
		}
	}
}

type histogramPoint struct {
	Count          uint64    `json:"count"`
	Sum            float64   `json:"sum"`
	BucketCounts   []uint64  `json:"bucketCounts"`
	ExplicitBounds []float64 `json:"explicitBounds"`
}

func pointsOf(t *testing.T, body map[string]any) []histogramPoint {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("encoding: %v", err)
	}
	var walked struct {
		ResourceMetrics []struct {
			ScopeMetrics []struct {
				Metrics []struct {
					Name      string `json:"name"`
					Histogram struct {
						AggregationTemporality int              `json:"aggregationTemporality"`
						DataPoints             []histogramPoint `json:"dataPoints"`
					} `json:"histogram"`
				} `json:"metrics"`
			} `json:"scopeMetrics"`
		} `json:"resourceMetrics"`
	}
	if err := json.Unmarshal(encoded, &walked); err != nil {
		t.Fatalf("decoding: %v", err)
	}
	metric := walked.ResourceMetrics[0].ScopeMetrics[0].Metrics[0]
	// 2 is AGGREGATION_TEMPORALITY_CUMULATIVE. Delta would make the read's
	// subtraction meaningless and the suite would be asserting nothing.
	if metric.Histogram.AggregationTemporality != 2 {
		t.Fatalf("want cumulative temporality (2), got %d", metric.Histogram.AggregationTemporality)
	}
	return metric.Histogram.DataPoints
}

func TestAnArrivalIsCountedAgainstItsOwnBaseline(t *testing.T) {
	cfg := settings(t)
	// The regression this guards: bounding the count by a wall-clock window let a
	// second run inside that window be satisfied by the first run's rows, and the
	// seeder returned in 7ms having waited for nothing. A statement with no time
	// bound cannot do that, because the older rows are in the baseline.
	for _, arrival := range arrivals(cfg) {
		if arrival.Expect < 1 {
			t.Errorf("%s expects %d rows, which no wait can confirm", arrival.What, arrival.Expect)
		}
		if strings.Contains(arrival.Statement, "received_at") ||
			strings.Contains(arrival.Statement, "timestamp") {
			t.Errorf(
				"%s bounds its count by time: %q. A baseline is what separates this "+
					"run's rows from the last one's, not a window.",
				arrival.What, arrival.Statement,
			)
		}
	}
}

// attributesOf flattens a span's attribute list into a map, the shape every
// assertion below actually wants to read.
func attributesOf(t *testing.T, span map[string]any) map[string]string {
	t.Helper()
	encoded, err := json.Marshal(span[keyAttributes])
	if err != nil {
		t.Fatalf("encoding attributes: %v", err)
	}
	var walked []struct {
		Key   string `json:"key"`
		Value struct {
			StringValue string `json:"stringValue"`
		} `json:"value"`
	}
	if err := json.Unmarshal(encoded, &walked); err != nil {
		t.Fatalf("decoding attributes: %v", err)
	}
	out := make(map[string]string, len(walked))
	for _, attr := range walked {
		out[attr.Key] = attr.Value.StringValue
	}
	return out
}

func TestGenAISpansSendsThreeCallsEachItsOwnTrace(t *testing.T) {
	cfg := settings(t)
	spans := spansOf(t, GenAISpans(cfg, time.Now()))

	if len(spans) != 3 {
		t.Fatalf("want 3 spans, got %d", len(spans))
	}
	seen := map[string]bool{}
	for _, span := range spans {
		id, _ := span["traceId"].(string)
		if id == "" || seen[id] {
			t.Fatalf("trace id %q missing or reused: each call must be its own trace", id)
		}
		seen[id] = true
		if kind, _ := span["kind"].(float64); kind != 3 {
			t.Errorf("kind = %v, want 3 (SPAN_KIND_CLIENT): a model call is a call to an external service", kind)
		}
		if _, ok := attributesOf(t, span)["gen_ai.provider.name"]; !ok {
			t.Errorf("span %q carries no gen_ai.provider.name: every seeded call must, or the "+
				"arrival query in main.go undercounts", span[keyName])
		}
	}
}

func TestGenAISpansCoversTheThreeCasesF29MustTellApart(t *testing.T) {
	cfg := settings(t)
	spans := spansOf(t, GenAISpans(cfg, time.Now()))

	complete, unlabeled, renamed := attributesOf(t, spans[0]), attributesOf(t, spans[1]), attributesOf(t, spans[2])

	if complete["gen_ai.request.model"] == "" || complete["gen_ai.usage.input_tokens"] == "" {
		t.Error("the first span must carry both a model name and token usage: it is the happy-path row")
	}

	if _, ok := unlabeled["gen_ai.request.model"]; ok {
		t.Error("the second span must carry no model-name field: it is the \"unlabeled model\" case")
	}
	if _, ok := unlabeled["gen_ai.usage.input_tokens"]; ok {
		t.Error("the second span must carry no usage field: it also proves a missing reading, not a zero one")
	}

	if _, ok := renamed["gen_ai.request.model"]; ok {
		t.Error("the third span must NOT carry gen_ai.request.model: it stands in for that field being renamed")
	}
	if renamed["gen_ai.request.model_name"] == "" {
		t.Error("the third span must carry its model name under a field this feature does not read")
	}
	if renamed["gen_ai.provider.name"] == "" {
		t.Error("the third span must still carry gen_ai.provider.name: only the model field is meant to be renamed")
	}
}

func TestCountedAtLeastReadsClickHousesAnswer(t *testing.T) {
	for _, row := range []struct {
		answer  string
		atLeast int
		want    bool
	}{
		{"80", 80, true},
		{"81", 80, true},
		{"79", 80, false},
		{"0", 1, false},
		// An empty or unparseable answer is "not yet", never "good enough":
		// treating it as a pass is how a gate seeds nothing and says it did.
		{"", 1, false},
		{"nonsense", 1, false},
	} {
		if got := countedAtLeast(row.answer, row.atLeast); got != row.want {
			t.Errorf("countedAtLeast(%q, %d) = %v, want %v", row.answer, row.atLeast, got, row.want)
		}
	}
}
