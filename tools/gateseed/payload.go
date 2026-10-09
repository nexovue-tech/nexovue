package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strconv"
	"time"
)

// BOUNDS is the histogram's ladder, in milliseconds.
//
// **Exactly fifteen, and that is load-bearing.** `metrics_live` asserts sixteen
// bucket counts against fifteen bounds, because a histogram has one more bucket
// than bounds — the last one is unbounded. It is the standard latency ladder an
// OTel SDK ships with, which is the point: the suite reads a shape a customer
// really sends, not one invented to pass it.
var BOUNDS = []float64{5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000, 30000}

// OTLP's own field names, spelled once. The linter asks for this and it is
// right to: a misspelt key in one of four places is a field the receiver
// silently ignores.
const (
	keyAttributes  = "attributes"
	keyName        = "name"
	keyResource    = "resource"
	keyStartUnixNs = "startTimeUnixNano"
)

// id returns a random OTLP id: 16 bytes for a trace, 8 for a span, hex encoded.
func id(bytes int) string {
	raw := make([]byte, bytes)
	if _, err := rand.Read(raw); err != nil {
		// crypto/rand does not fail on any supported platform; if it ever does,
		// seeding with predictable ids would be worse than stopping.
		panic(fmt.Sprintf("reading randomness for an OTLP id: %v", err))
	}
	return hex.EncodeToString(raw)
}

func nanos(at time.Time) string {
	return strconv.FormatInt(at.UnixNano(), 10)
}

func attribute(key, value string) map[string]any {
	return map[string]any{"key": key, "value": map[string]any{"stringValue": value}}
}

// resource names the service every seeded signal belongs to. One service, named
// after this tool, so anything it leaves behind is obviously ours.
func resource(serviceName string) map[string]any {
	return map[string]any{
		keyAttributes: []any{attribute("service.name", serviceName)},
	}
}

// SlowEnough is how long a seeded trace takes.
//
// **Deliberately past the sampler's threshold.** Tail sampling keeps every
// error, every trace slower than `WORKERS_SAMPLING_SLOW_THRESHOLD` (2s as
// shipped), and one healthy trace in ten. Eighty fast, healthy traces therefore
// became eleven rows — measured — which is the sampler working correctly and
// the seeder asking for the wrong thing. Sending eight hundred instead would
// rest on a ratio holding; sending slow ones is deterministic.
//
// Slow rather than failing, because eighty seeded *errors* every gate run would
// show up on the Errors screen as a fault nobody has. They are their own
// service, so no real service's latency moves either.
const SlowEnough = 2500 * time.Millisecond

// Traces builds one OTLP/JSON export: `count` root spans, each its own trace.
//
// **One of them carries an exception event.** The worker promotes a span event
// named `exception` into the `exception_type` column, and the error-group reads
// are built on that column — which is why an exception recorded as a *span
// attribute* does not satisfy them and why only the demo could produce this
// before. One is enough: the suite asks whether any exists, not how many.
func Traces(cfg Config, count int, at time.Time) map[string]any {
	spans := make([]any, 0, count)
	for i := range count {
		start := at.Add(-time.Duration(i) * time.Millisecond)
		span := map[string]any{
			"traceId":         id(16),
			"spanId":          id(8),
			keyName:           "GET /seeded",
			"kind":            2, // SPAN_KIND_SERVER
			keyStartUnixNs:    nanos(start),
			"endTimeUnixNano": nanos(start.Add(SlowEnough)),
			keyAttributes: []any{
				attribute("http.request.method", "GET"),
				attribute("url.path", "/seeded"),
			},
		}
		if i == 0 {
			span["status"] = map[string]any{"code": 2, "message": "seeded failure"}
			span["events"] = []any{map[string]any{
				"timeUnixNano": nanos(start.Add(SlowEnough / 2)),
				keyName:        "exception",
				keyAttributes: []any{
					attribute("exception.type", "GateSeedError"),
					attribute("exception.message", "a seeded exception, so the error-group reads have one"),
				},
			}}
		}
		spans = append(spans, span)
	}

	return map[string]any{"resourceSpans": []any{map[string]any{
		keyResource:  resource(cfg.ServiceName),
		"scopeSpans": []any{map[string]any{"spans": spans}},
	}}}
}

// Metrics builds one OTLP/JSON export of a cumulative histogram.
//
// **Cumulative, and rising.** Each series reports `Exports` points whose counts
// only grow, which is what every real SDK does and what the read under test has
// to undo: summing the points would count one observation once per export. A
// single point per series would make the naive sum and the window's delta the
// same number, and the test that catches that confusion would pass on a shape
// that cannot exhibit it.
func Metrics(cfg Config, at time.Time) map[string]any {
	points := make([]any, 0, cfg.Series*cfg.Exports)
	for series := range cfg.Series {
		// A distinct attribute set per series, so the read has several to merge
		// over a shared set of bounds.
		route := "/seeded/" + strconv.Itoa(series)
		// `series` counts up from zero, so this conversion cannot be negative.
		seriesWeight := uint64(series)
		counts, sum := make([]uint64, len(BOUNDS)+1), 0.0
		for export := 1; export <= cfg.Exports; export++ {
			// Every export adds observations spread across the ladder, weighted
			// low, so the percentiles come out ordered and inside the bounds
			// rather than piled into the unbounded last bucket.
			//
			// Counted down from the length rather than computed from the index,
			// so every term is a `uint64` by construction and nothing has to be
			// converted from a signed number that might, in principle, be
			// negative.
			weight := uint64(len(counts))
			for bucket := range counts {
				added := weight + seriesWeight
				counts[bucket] += added
				sum += float64(added) * representative(bucket)
				weight--
			}
			points = append(points, map[string]any{
				keyStartUnixNs:   nanos(at.Add(-time.Duration(cfg.Exports) * 10 * time.Second)),
				"timeUnixNano":   nanos(at.Add(-time.Duration(cfg.Exports-export) * 10 * time.Second)),
				"count":          total(counts),
				"sum":            sum,
				"bucketCounts":   copied(counts),
				"explicitBounds": BOUNDS,
				keyAttributes:    []any{attribute("http.route", route)},
			})
		}
	}

	metric := map[string]any{
		"name": cfg.MetricName,
		"unit": "ms",
		"histogram": map[string]any{
			// AGGREGATION_TEMPORALITY_CUMULATIVE.
			"aggregationTemporality": 2,
			"dataPoints":             points,
		},
	}
	scope := map[string]any{"metrics": []any{metric}}
	return map[string]any{"resourceMetrics": []any{map[string]any{
		keyResource:    resource(cfg.ServiceName),
		"scopeMetrics": []any{scope},
	}}}
}

// GenAISpans builds one OTLP/JSON export of three model-call spans, each its
// own trace, for F29 (LLM observability).
//
// **Three, not one, because the screen's hardest requirement is what happens
// when a field is missing or renamed, not the happy path.** `market-analysis-
// 2026-09.md` §13: the `gen_ai.*` convention has already broken one field name
// once (`gen_ai.system` → `gen_ai.provider.name`, Aug 2025) and carries no
// stability guarantee yet, which is why F29's spec identifies a model call
// generically — any `gen_ai.`-prefixed attribute — rather than by a fixed
// schema. These three spans are what proves that holds:
//
//  1. every current field present (`gen_ai.provider.name`, `gen_ai.request.model`,
//     `gen_ai.response.model`, `gen_ai.usage.input_tokens/output_tokens`) — the
//     row a customer mostly sends;
//  2. a provider present but no model-name field at all — the "unlabeled
//     model" bucket, and no token usage either, so the same row also proves a
//     missing reading renders as an em dash rather than a false zero;
//  3. a model-name field under a name this screen does not look for — standing
//     in for the next rename before it happens, so AC29.6 has something to
//     assert against without waiting for the convention to actually move
//     again.
//
// Current field names confirmed 2026-10-09 against the semantic-conventions
// repository, not assumed — the whole reason these spans have to be real
// enough to query is that an invented shape could not catch a real drift.
func GenAISpans(cfg Config, at time.Time) map[string]any {
	start := func(offset int) time.Time { return at.Add(-time.Duration(offset) * time.Millisecond) }
	span := func(start time.Time, name string, attrs ...any) any {
		return map[string]any{
			"traceId":         id(16),
			"spanId":          id(8),
			keyName:           name,
			"kind":            3, // SPAN_KIND_CLIENT: a call to an external model provider.
			keyStartUnixNs:    nanos(start),
			"endTimeUnixNano": nanos(start.Add(SlowEnough)),
			keyAttributes:     attrs,
		}
	}

	spans := []any{
		span(start(0), "chat claude-fable-5-1",
			attribute("gen_ai.operation.name", "chat"),
			attribute("gen_ai.provider.name", "anthropic"),
			attribute("gen_ai.request.model", "claude-fable-5-1"),
			attribute("gen_ai.response.model", "claude-fable-5-1"),
			attribute("gen_ai.usage.input_tokens", "842"),
			attribute("gen_ai.usage.output_tokens", "156"),
		),
		span(start(1), "chat",
			attribute("gen_ai.operation.name", "chat"),
			attribute("gen_ai.provider.name", "openai"),
			// Deliberately no request.model, response.model, or usage — the
			// "unlabeled model" and "missing reading" cases in one span.
		),
		span(start(2), "chat gen_ai_test_model",
			attribute("gen_ai.operation.name", "chat"),
			attribute("gen_ai.provider.name", "anthropic"),
			// A model-name field under a name this feature does not read,
			// standing in for a future rename of gen_ai.request.model.
			attribute("gen_ai.request.model_name", "gen_ai_test_model"),
			attribute("gen_ai.usage.input_tokens", "310"),
			attribute("gen_ai.usage.output_tokens", "48"),
		),
	}

	return map[string]any{"resourceSpans": []any{map[string]any{
		keyResource:  resource(cfg.ServiceName),
		"scopeSpans": []any{map[string]any{"spans": spans}},
	}}}
}

// representative is a value inside a bucket, used to keep `sum` consistent with
// the counts. The unbounded last bucket is represented by its lower bound.
func representative(bucket int) float64 {
	if bucket == 0 {
		return BOUNDS[0] / 2
	}
	if bucket >= len(BOUNDS) {
		return BOUNDS[len(BOUNDS)-1]
	}
	return (BOUNDS[bucket-1] + BOUNDS[bucket]) / 2
}

func total(counts []uint64) uint64 {
	var sum uint64
	for _, count := range counts {
		sum += count
	}
	return sum
}

// copied snapshots the running counts, because the slice keeps being added to
// and every point would otherwise serialise the final state.
func copied(counts []uint64) []uint64 {
	out := make([]uint64, len(counts))
	copy(out, counts)
	return out
}
