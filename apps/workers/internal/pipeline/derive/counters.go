// Package derive computes the exact per-minute aggregates that the screens,
// the dashboards and anomaly detection read: the counter points of
// `metrics_sum`, and the endpoint rollup `endpoint_stats_1m`.
//
// It runs before the sampler, over every span of every trace. That order is the
// whole point (ADR 0010): storage can drop nine healthy traces in ten and the
// counts, error rates and totals stay exact, because they were computed before
// anything was dropped. `endpoint_stats_1m` was filled by a materialized view
// over `spans` until 000024, which put it *downstream* of the sampler and made
// the service overview read a tenth of the traffic at ten times the error rate.
//
// Both outputs come out of one Observe and one drain, so a row of the rollup
// and the counters beside it can never disagree about the same span.
package derive

import (
	"encoding/json"
	"sort"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/clocktrust"
)

// The derived metric names. They are a stable contract — a dashboard, an alert
// rule and a stored query all name them — so they are constants here and are
// never built by concatenation.
const (
	// MetricSpanCount counts spans seen, before sampling.
	MetricSpanCount = "nexovue.spans.count"
	// MetricSpanErrors counts spans whose status is ERROR, before sampling.
	MetricSpanErrors = "nexovue.spans.error.count"
	// MetricSpanDurationSum totals span durations in milliseconds, before
	// sampling. With the count it gives an exact mean; percentiles are in
	// `endpoint_stats_1m`, which this stage also fills.
	MetricSpanDurationSum = "nexovue.spans.duration_ms.sum"
)

// Attribute keys on the derived rows. service_name is a column of its own, so
// it is not repeated here.
const (
	attrEndpoint   = "endpoint"
	attrHTTPMethod = "http_method"
)

// The bucket width and the clock-trust bounds are clocktrust's, so the counters
// and the edge accumulator cannot end up disagreeing about which clock a row is
// bucketed on.

// Span is one span as the counters see it. Endpoint is already resolved by the
// caller, which owns the semantic-convention rules for finding it.
type Span struct {
	TenantID    string
	ProjectID   string
	ServiceName string
	Endpoint    string
	HTTPMethod  string

	// SpanKind and SpanName are the endpoint rollup's own dimensions, and are
	// the table's rather than this stage's: an HTTP route and a queue handler
	// can share a name and are not the same endpoint. Only SERVER and CONSUMER
	// spans reach it.
	SpanKind string
	SpanName string

	// TraceID and SpanID identify the span, and are what weights its duration
	// in the rollup's latency sample. A span id is unique only within a trace,
	// so both are needed: a broken SDK emitting a constant id would otherwise
	// weight every one of its spans the same.
	TraceID []byte
	SpanID  []byte

	// Timestamp is when the client says the span started.
	Timestamp time.Time

	// RetentionDays is the derived-signal retention of the project this span
	// belongs to. It reaches the counter row so its part can expire whole.
	RetentionDays uint16
	// ReceivedAt is when we took the message carrying it — the only clock in
	// the path we control, and so what Timestamp is judged against. A zero
	// value leaves Timestamp unjudged; the caller is expected to always set it.
	ReceivedAt time.Time

	DurationMS float64
	Errored    bool

	// Partition is the Kafka partition the span's record arrived on. It is
	// provenance rather than a dimension — no row carries it — but the buckets
	// keep each partition's share apart so that a partition handed to another
	// worker can have its contribution taken back out (ADR 0094). The topic is
	// implicit: one set of counters serves one pipeline, and a pipeline consumes
	// one topic.
	Partition int32
}

// Point is one row of the `metrics` table.
type Point struct {
	Timestamp     time.Time
	RetentionDays uint16
	TenantID      string
	ProjectID     string
	MetricName    string
	Value         float64
	ServiceName   string
	Attrs         string
}

// interval is the window this point covers: OTLP delta sums carry both ends,
// and the bucket runs from its own minute to the next. Computed in one place so
// the inserted row and the read-back that checks for it cannot disagree.
func (p Point) interval() (start, end time.Time) {
	return p.Timestamp, p.Timestamp.Add(clocktrust.Bucket)
}

// series identifies one counted line: a minute, a tenancy, a service and an
// endpoint. It is a comparable struct so it can key a map without a formatted
// string per span.
type series struct {
	minute     time.Time
	tenantID   string
	projectID  string
	service    string
	endpoint   string
	httpMethod string
	// Constant for a given project, but carried here so the drained point knows
	// it without a second lookup. Part of the counter table's partition key.
	retentionDays uint16
}

// counted is one partition's share of one series. The buckets are keyed by it
// rather than by the series alone so that a revoke can drop a partition's
// contribution; the shares are summed back together at drain time, and no row
// ever carries the partition.
type counted struct {
	series
	partition int32
}

// before orders shares so that the ones belonging to a series are adjacent and
// in ascending partition order. Folding them in that order is what keeps a
// re-derivation of the same offsets summing the same floating-point total, and
// so producing the block ClickHouse recognises as a repeat.
func (c counted) before(other counted) bool {
	switch {
	case !c.minute.Equal(other.minute):
		return c.minute.Before(other.minute)
	case c.tenantID != other.tenantID:
		return c.tenantID < other.tenantID
	case c.projectID != other.projectID:
		return c.projectID < other.projectID
	case c.service != other.service:
		return c.service < other.service
	case c.endpoint != other.endpoint:
		return c.endpoint < other.endpoint
	case c.httpMethod != other.httpMethod:
		return c.httpMethod < other.httpMethod
	case c.retentionDays != other.retentionDays:
		return c.retentionDays < other.retentionDays
	default:
		return c.partition < other.partition
	}
}

type totals struct {
	spans      float64
	errors     float64
	durationMS float64
}

// add folds one partition's share into a running total for its series.
func (t *totals) add(share totals) {
	t.spans += share.spans
	t.errors += share.errors
	t.durationMS += share.durationMS
}

// Counters accumulate per-minute totals in memory and hand out rows once a
// minute is complete.
//
// It is not safe for concurrent use: one pipeline goroutine owns it, which is
// how the trace-assembly loop is built.
type Counters struct {
	buckets map[counted]*totals
	// endpoints is the second output, at the endpoint rollup's own grain. It is
	// a separate map rather than a wider key because `metrics_sum` covers every
	// span kind, and folding span_name into its series would multiply that
	// table by a dimension only this rollup reads.
	endpoints map[countedEndpoint]*endpointTotals
	lateGrace time.Duration
}

// Derived is one drain: the counter points and the endpoint rollup rows for the
// same minutes. They are handed out together because they are the same spans
// counted twice over, and a caller that wrote one without the other would leave
// the two disagreeing until the next restart.
type Derived struct {
	Points    []Point
	Endpoints []EndpointStat
}

// Empty reports whether a drain produced nothing at all.
func (d Derived) Empty() bool { return len(d.Points) == 0 && len(d.Endpoints) == 0 }

// NewCounters returns counters that hold a minute open for lateGrace after it
// ends, so spans that arrive a little late still land in their own minute.
//
// A span arriving after that reopens its minute and is flushed as a second row
// for it. The table is summed on read, so two rows for one minute total the
// same as one — late data costs a row, never accuracy.
func NewCounters(lateGrace time.Duration) *Counters {
	return &Counters{
		buckets:   make(map[counted]*totals),
		endpoints: make(map[countedEndpoint]*endpointTotals),
		lateGrace: lateGrace,
	}
}

// Observe counts one span and returns the minute bucket it landed in. Every
// span is counted exactly once, whatever the sampler later decides about the
// trace it belongs to; the returned minute is what lets the caller know when
// this span's counters have reached storage.
//
// discardedSkew is how far the span's own timestamp sat from receive time when
// that timestamp was not trusted, and zero when it was. It is returned rather
// than logged here so the caller can report it once per batch instead of once
// per span, and can name the tenant whose clock is wrong.
func (c *Counters) Observe(now time.Time, span Span) (minute time.Time, discardedSkew time.Duration) {
	at, discarded := clocktrust.Instant(span.Timestamp, span.ReceivedAt, now)
	key := counted{
		series: series{
			minute:        at.UTC().Truncate(clocktrust.Bucket),
			tenantID:      span.TenantID,
			projectID:     span.ProjectID,
			service:       span.ServiceName,
			endpoint:      span.Endpoint,
			httpMethod:    span.HTTPMethod,
			retentionDays: span.RetentionDays,
		},
		partition: span.Partition,
	}
	bucket, ok := c.buckets[key]
	if !ok {
		bucket = &totals{}
		c.buckets[key] = bucket
	}
	bucket.spans++
	bucket.durationMS += span.DurationMS
	if span.Errored {
		bucket.errors++
	}
	c.observeEndpoint(key.minute, span)
	return key.minute, discarded
}

// Drained reports whether everything derived for a minute has already been
// written. One answer covers both outputs: they share the bucket and the grace,
// and they are drained in the same call.
//
// A zero minute means nothing was counted, which is trivially drained.
func (c *Counters) Drained(now, minute time.Time) bool {
	if minute.IsZero() {
		return true
	}
	return !minute.Add(clocktrust.Bucket).After(now.UTC().Add(-c.lateGrace))
}

// Drain returns rows for every minute that has ended and outlived the grace
// period, removing them from memory. Minutes still open are left alone.
func (c *Counters) Drain(now time.Time) Derived {
	deadline := now.UTC().Add(-c.lateGrace)
	return c.drain(func(minute time.Time) bool {
		return !minute.Add(clocktrust.Bucket).After(deadline)
	})
}

// DrainAll returns rows for every minute held, complete or not. Shutdown calls
// it: an unwritten counter is a hole in a graph that nothing will ever fill.
func (c *Counters) DrainAll() Derived {
	return c.drain(func(time.Time) bool { return true })
}

// drain sums each series' per-partition shares back together and hands out one
// set of rows per series, exactly as if the partitions had never been apart.
func (c *Counters) drain(closed func(minute time.Time) bool) Derived {
	due := make([]counted, 0, len(c.buckets))
	for key := range c.buckets {
		if closed(key.minute) {
			due = append(due, key)
		}
	}
	sort.Slice(due, func(i, j int) bool { return due[i].before(due[j]) })

	points := make([]Point, 0, len(due)*3)
	var (
		line    series
		running totals
		open    bool
	)
	for _, key := range due {
		if open && key.series != line {
			points = append(points, line.points(running)...)
			running = totals{}
		}
		line, open = key.series, true
		running.add(*c.buckets[key])
		delete(c.buckets, key)
	}
	if open {
		points = append(points, line.points(running)...)
	}

	// Sorted because the block ClickHouse receives must be byte-identical when a
	// restart re-derives the same minute from the same offsets — that is what
	// lets the store's own insert deduplication recognise the retry and drop it.
	// Go randomises map iteration, so without this the same counts produce a
	// different block every time and every replay double-counts.
	sort.Slice(points, func(i, j int) bool { return points[i].less(points[j]) })
	return Derived{Points: points, Endpoints: c.drainEndpoints(closed)}
}

// points is the three rows one series' totals become.
func (s series) points(summed totals) []Point {
	attrs := s.attributesJSON()
	return []Point{
		s.point(MetricSpanCount, summed.spans, attrs),
		s.point(MetricSpanErrors, summed.errors, attrs),
		s.point(MetricSpanDurationSum, summed.durationMS, attrs),
	}
}

// Disowned is what leaving a partition cost the counters: the shares taken out
// of both maps, and the spans whose counts went with them.
type Disowned struct {
	Series    int
	Endpoints int
	Spans     uint64
}

// Disown drops every share that arrived on one of the given partitions, from
// both the counter buckets and the endpoint rollup.
//
// This is not lost data, and the reason is the ordering the offsets already
// rest on: a batch's offsets — and its durable counted-through watermark — move
// only once every minute bucket it fed has been drained, so anything still in
// an open bucket belongs to records the broker has not been told about. The
// worker that takes the partition over reads those records again and counts
// them. Keeping them is what counts them twice, and a counter counted twice
// cannot be repaired: `metrics_sum` is summed on read and the individual
// contributions are gone (ADR 0094).
func (c *Counters) Disown(partitions []int32) Disowned {
	if len(partitions) == 0 {
		return Disowned{}
	}
	leaving := make(map[int32]struct{}, len(partitions))
	for _, partition := range partitions {
		leaving[partition] = struct{}{}
	}

	var dropped Disowned
	for key, bucket := range c.buckets {
		if _, gone := leaving[key.partition]; !gone {
			continue
		}
		dropped.Series++
		// Every increment is exactly one span, so the total is an exact integer.
		dropped.Spans += uint64(bucket.spans)
		delete(c.buckets, key)
	}
	for key := range c.endpoints {
		if _, gone := leaving[key.partition]; !gone {
			continue
		}
		dropped.Endpoints++
		delete(c.endpoints, key)
	}
	return dropped
}

// less orders points totally, so a drain is reproducible.
func (p Point) less(other Point) bool {
	switch {
	case !p.Timestamp.Equal(other.Timestamp):
		return p.Timestamp.Before(other.Timestamp)
	case p.TenantID != other.TenantID:
		return p.TenantID < other.TenantID
	case p.ProjectID != other.ProjectID:
		return p.ProjectID < other.ProjectID
	case p.ServiceName != other.ServiceName:
		return p.ServiceName < other.ServiceName
	case p.Attrs != other.Attrs:
		return p.Attrs < other.Attrs
	default:
		return p.MetricName < other.MetricName
	}
}

// Pending reports how many per-partition counter shares are held, which is what
// the pipeline logs and what shows a leak if one ever appears. Shares rather
// than series, because the shares are what occupy memory; one series spread
// over eight partitions is eight of them.
func (c *Counters) Pending() int { return len(c.buckets) }

// PendingEndpoints reports how many endpoint-minute shares are held. Logged
// separately because it is the map that can grow with a customer's span-name
// cardinality, and each of its buckets holds a latency sample rather than three
// floats.
func (c *Counters) PendingEndpoints() int { return len(c.endpoints) }

func (s series) point(name string, value float64, attrs string) Point {
	return Point{
		Timestamp:     s.minute,
		RetentionDays: s.retentionDays,
		TenantID:      s.tenantID,
		ProjectID:     s.projectID,
		MetricName:    name,
		Value:         value,
		ServiceName:   s.service,
		Attrs:         attrs,
	}
}

// attributesJSON renders the dimensions that are not columns. An absent method
// is omitted rather than written empty, so a non-HTTP span's rows do not carry
// a field that means nothing.
func (s series) attributesJSON() string {
	values := map[string]string{attrEndpoint: s.endpoint}
	if s.httpMethod != "" {
		values[attrHTTPMethod] = s.httpMethod
	}
	encoded, err := json.Marshal(values)
	if err != nil {
		// Both values are plain strings, so this cannot fail; an empty document
		// keeps the row insertable if it somehow did.
		return "{}"
	}
	return string(encoded)
}
