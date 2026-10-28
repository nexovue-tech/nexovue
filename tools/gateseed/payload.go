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
	keyEndUnixNs   = "endTimeUnixNano"
	keyTimeUnixNs  = "timeUnixNano"
	keyKey         = "key"
	keyTraceID     = "traceId"
	keySpanID      = "spanId"
	keyKind        = "kind"
	keyResourceSp  = "resourceSpans"
	keyScopeSpans  = "scopeSpans"
	keyValue       = "value"
	keySpans       = "spans"
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
	return map[string]any{keyKey: key, keyValue: map[string]any{"stringValue": value}}
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
			keyTraceID:     id(16),
			keySpanID:      id(8),
			keyName:        "GET /seeded",
			keyKind:        2, // SPAN_KIND_SERVER
			keyStartUnixNs: nanos(start),
			keyEndUnixNs:   nanos(start.Add(SlowEnough)),
			keyAttributes: []any{
				attribute("http.request.method", "GET"),
				attribute("url.path", "/seeded"),
			},
		}
		if i == 0 {
			span["status"] = map[string]any{"code": 2, "message": "seeded failure"}
			span["events"] = []any{map[string]any{
				keyTimeUnixNs: nanos(start.Add(SlowEnough / 2)),
				keyName:       "exception",
				keyAttributes: []any{
					attribute("exception.type", "GateSeedError"),
					attribute("exception.message", "a seeded exception, so the error-group reads have one"),
				},
			}}
		}
		spans = append(spans, span)
	}

	return map[string]any{keyResourceSp: []any{map[string]any{
		keyResource:   resource(cfg.ServiceName),
		keyScopeSpans: []any{map[string]any{keySpans: spans}},
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
				keyTimeUnixNs:    nanos(at.Add(-time.Duration(cfg.Exports-export) * 10 * time.Second)),
				"count":          total(counts),
				"sum":            sum,
				"bucketCounts":   copied(counts),
				"explicitBounds": BOUNDS,
				keyAttributes:    []any{attribute("http.route", route)},
			})
		}
	}

	metric := cumulativeHistogram(cfg.MetricName, "ms", points)
	// The same observations under the HTTP convention's own name, in its unit, because that name is
	// what the latency heatmap looks for (metrics/queries.py, DURATION_METRIC_NAMES) — without it the
	// gate needs the demo shop's traffic for that suite.
	duration := cumulativeHistogram(httpServerDuration, "s", inSeconds(points))
	scope := map[string]any{"metrics": []any{metric, duration}}
	return map[string]any{"resourceMetrics": []any{map[string]any{
		keyResource:    resource(cfg.ServiceName),
		"scopeMetrics": []any{scope},
	}}}
}

// httpServerDuration is OpenTelemetry's HTTP server duration metric, recorded in seconds.
const httpServerDuration = "http.server.request.duration"

// millisecondsPerSecond converts the ladder BOUNDS (milliseconds) to the convention's seconds.
const millisecondsPerSecond = 1000.0

func cumulativeHistogram(name, unit string, points []any) map[string]any {
	return map[string]any{
		"name": name,
		"unit": unit,
		"histogram": map[string]any{
			// AGGREGATION_TEMPORALITY_CUMULATIVE.
			"aggregationTemporality": 2,
			"dataPoints":             points,
		},
	}
}

// inSeconds copies millisecond points with their bounds and sums in seconds; counts are unchanged.
func inSeconds(points []any) []any {
	converted := make([]any, 0, len(points))
	for _, raw := range points {
		point, _ := raw.(map[string]any)
		copied := make(map[string]any, len(point))
		for key, value := range point {
			copied[key] = value
		}
		bounds, _ := point["explicitBounds"].([]float64)
		seconds := make([]float64, len(bounds))
		for i, bound := range bounds {
			seconds[i] = bound / millisecondsPerSecond
		}
		copied["explicitBounds"] = seconds
		if sum, ok := point["sum"].(float64); ok {
			copied["sum"] = sum / millisecondsPerSecond
		}
		converted = append(converted, copied)
	}
	return converted
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
			keyTraceID:     id(16),
			keySpanID:      id(8),
			keyName:        name,
			keyKind:        3, // SPAN_KIND_CLIENT: a call to an external model provider.
			keyStartUnixNs: nanos(start),
			keyEndUnixNs:   nanos(start.Add(SlowEnough)),
			keyAttributes:  attrs,
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

	return map[string]any{keyResourceSp: []any{map[string]any{
		keyResource:   resource(cfg.ServiceName),
		keyScopeSpans: []any{map[string]any{keySpans: spans}},
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

// EdgeLead is how far in the past the seeded calls are timestamped.
//
// A service edge is a per-minute sum, written only once its minute has closed —
// a minute, plus the pairing window, plus the late grace. Seeded now, the gate
// would wait up to a minute for its edges; seeded three minutes back, the minute
// is already closed and the edges are written on the worker's next settle. Well
// inside the seven days of past skew the worker trusts.
const EdgeLead = 3 * time.Minute

// Edge service names, all derived from the seeder's own so they are obviously
// ours on the service map.
func callerService(cfg Config) string  { return cfg.ServiceName + "-caller" }
func healthyService(cfg Config) string { return cfg.ServiceName + "-healthy" }
func failingService(cfg Config) string { return cfg.ServiceName + "-failing" }

// ServiceEdges builds one OTLP/JSON export of two calls: a CLIENT span in the
// caller answered by a SERVER child in a callee — the pairing the worker derives
// an edge from — once to a healthy callee and once to a failing one.
//
// **Two edges, one failing.** The service-map suite proves its "failing only"
// filter narrows the map, which needs a failing edge to keep and a healthy one to
// drop. Until this, only the OpenTelemetry demo's own traffic supplied either.
func ServiceEdges(cfg Config, at time.Time) map[string]any {
	call := func(callee string, failed bool) (client, server map[string]any) {
		traceID, clientID := id(16), id(8)
		client = map[string]any{
			keyTraceID: traceID, keySpanID: clientID, keyName: "call " + callee,
			keyKind:        3, // SPAN_KIND_CLIENT
			keyStartUnixNs: nanos(at), keyEndUnixNs: nanos(at.Add(20 * time.Millisecond)),
		}
		server = map[string]any{
			keyTraceID: traceID, keySpanID: id(8), "parentSpanId": clientID, keyName: "serve",
			keyKind:        2, // SPAN_KIND_SERVER
			keyStartUnixNs: nanos(at.Add(time.Millisecond)),
			keyEndUnixNs:   nanos(at.Add(19 * time.Millisecond)),
		}
		if failed {
			failure := map[string]any{"code": 2, "message": "seeded failing dependency"}
			client["status"], server["status"] = failure, failure
		}
		return client, server
	}
	healthyClient, healthyServer := call(healthyService(cfg), false)
	failingClient, failingServer := call(failingService(cfg), true)

	spansOf := func(service string, spans ...any) map[string]any {
		return map[string]any{
			keyResource:   resource(service),
			keyScopeSpans: []any{map[string]any{keySpans: spans}},
		}
	}
	return map[string]any{keyResourceSp: []any{
		spansOf(callerService(cfg), healthyClient, failingClient),
		spansOf(healthyService(cfg), healthyServer),
		spansOf(failingService(cfg), failingServer),
	}}
}

// DatabaseServer is the server address the seeded database readings name.
const DatabaseServer = "gateseed-postgres"

// DatabaseReadings builds one OTLP/JSON export of the two gauges the database
// screens read: a statement's mean duration and the connection count by state,
// with the attributes the collector's Postgres receiver attaches. Until this,
// only the OpenTelemetry demo's own Postgres supplied them.
func DatabaseReadings(cfg Config, at time.Time) map[string]any {
	gauge := func(name, unit string, points ...any) map[string]any {
		return map[string]any{keyName: name, "unit": unit, "gauge": map[string]any{"dataPoints": points}}
	}
	statement := func(hash string, seconds float64) map[string]any {
		return map[string]any{
			keyTimeUnixNs: nanos(at),
			"asDouble":    seconds,
			keyAttributes: []any{
				attribute("db.query.hash", hash),
				attribute("db.system.name", "postgresql"),
				attribute("db.namespace", cfg.ServiceName),
				map[string]any{keyKey: "db.client.operation.calls", keyValue: map[string]any{"intValue": "3"}},
			},
		}
	}
	connections := func(state string, count int) map[string]any {
		return map[string]any{
			keyTimeUnixNs: nanos(at),
			"asInt":       strconv.Itoa(count),
			keyAttributes: []any{attribute("db.client.connection.state", state)},
		}
	}
	databaseResource := map[string]any{keyAttributes: []any{
		attribute("service.name", cfg.ServiceName),
		attribute("server.address", DatabaseServer),
		map[string]any{keyKey: "server.port", keyValue: map[string]any{"intValue": "5432"}},
	}}
	return map[string]any{"resourceMetrics": []any{map[string]any{
		keyResource: databaseResource,
		"scopeMetrics": []any{map[string]any{"metrics": []any{
			gauge("db.client.operation.duration", "s",
				statement("gateseed-select", 0.012), statement("gateseed-update", 0.048)),
			gauge("db.client.connection.count", "{connection}",
				connections("idle", 4), connections("active", 1)),
		}}},
	}}}
}
