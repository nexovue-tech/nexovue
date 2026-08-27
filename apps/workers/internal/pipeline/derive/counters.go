// Package derive computes the exact per-minute counters that dashboards and
// anomaly detection read.
//
// It runs before the sampler, over every span of every trace. That order is the
// whole point (ADR 0010): storage can drop nine healthy traces in ten and the
// counts, error rates and totals stay exact, because they were computed before
// anything was dropped.
package derive

import (
	"encoding/json"
	"sort"
	"time"
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
	// sampling. With the count it gives an exact mean; percentiles need the
	// stored spans and are therefore only as exact as the sample.
	MetricSpanDurationSum = "nexovue.spans.duration_ms.sum"
)

// Attribute keys on the derived rows. service_name is a column of its own, so
// it is not repeated here.
const (
	attrEndpoint   = "endpoint"
	attrHTTPMethod = "http_method"
)

// bucketWidth is the resolution of the counters. Per-minute is the finest
// resolution a dashboard reads and the coarsest that still shows a spike.
const bucketWidth = time.Minute

// Span is one span as the counters see it. Endpoint is already resolved by the
// caller, which owns the semantic-convention rules for finding it.
type Span struct {
	TenantID    string
	ProjectID   string
	ServiceName string
	Endpoint    string
	HTTPMethod  string
	Timestamp   time.Time
	DurationMS  float64
	Errored     bool
}

// Point is one row of the `metrics` table.
type Point struct {
	Timestamp   time.Time
	TenantID    string
	ProjectID   string
	MetricName  string
	Value       float64
	ServiceName string
	Attrs       string
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
}

type totals struct {
	spans      float64
	errors     float64
	durationMS float64
}

// Counters accumulate per-minute totals in memory and hand out rows once a
// minute is complete.
//
// It is not safe for concurrent use: one pipeline goroutine owns it, which is
// how the trace-assembly loop is built.
type Counters struct {
	buckets   map[series]*totals
	lateGrace time.Duration
}

// NewCounters returns counters that hold a minute open for lateGrace after it
// ends, so spans that arrive a little late still land in their own minute.
//
// A span arriving after that reopens its minute and is flushed as a second row
// for it. The table is summed on read, so two rows for one minute total the
// same as one — late data costs a row, never accuracy.
func NewCounters(lateGrace time.Duration) *Counters {
	return &Counters{buckets: make(map[series]*totals), lateGrace: lateGrace}
}

// Observe counts one span and returns the minute bucket it landed in. Every
// span is counted exactly once, whatever the sampler later decides about the
// trace it belongs to; the returned minute is what lets the caller know when
// this span's counters have reached storage.
func (c *Counters) Observe(span Span) time.Time {
	key := series{
		minute:     span.Timestamp.UTC().Truncate(bucketWidth),
		tenantID:   span.TenantID,
		projectID:  span.ProjectID,
		service:    span.ServiceName,
		endpoint:   span.Endpoint,
		httpMethod: span.HTTPMethod,
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
	return key.minute
}

// Drained reports whether every counter for a minute has already been written.
// A zero minute means nothing was counted, which is trivially drained.
func (c *Counters) Drained(now, minute time.Time) bool {
	if minute.IsZero() {
		return true
	}
	return !minute.Add(bucketWidth).After(now.UTC().Add(-c.lateGrace))
}

// Drain returns rows for every minute that has ended and outlived the grace
// period, removing them from memory. Minutes still open are left alone.
func (c *Counters) Drain(now time.Time) []Point {
	deadline := now.UTC().Add(-c.lateGrace)
	return c.drain(func(minute time.Time) bool {
		return !minute.Add(bucketWidth).After(deadline)
	})
}

// DrainAll returns rows for every minute held, complete or not. Shutdown calls
// it: an unwritten counter is a hole in a graph that nothing will ever fill.
func (c *Counters) DrainAll() []Point {
	return c.drain(func(time.Time) bool { return true })
}

func (c *Counters) drain(closed func(minute time.Time) bool) []Point {
	points := make([]Point, 0, len(c.buckets)*3)
	for key, bucket := range c.buckets {
		if !closed(key.minute) {
			continue
		}
		attrs := key.attributesJSON()
		points = append(points,
			key.point(MetricSpanCount, bucket.spans, attrs),
			key.point(MetricSpanErrors, bucket.errors, attrs),
			key.point(MetricSpanDurationSum, bucket.durationMS, attrs),
		)
		delete(c.buckets, key)
	}

	// Sorted because the block ClickHouse receives must be byte-identical when a
	// restart re-derives the same minute from the same offsets — that is what
	// lets the store's own insert deduplication recognise the retry and drop it.
	// Go randomises map iteration, so without this the same counts produce a
	// different block every time and every replay double-counts.
	sort.Slice(points, func(i, j int) bool { return points[i].less(points[j]) })
	return points
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

// Pending reports how many series are held, which is what the pipeline logs and
// what shows a leak if one ever appears.
func (c *Counters) Pending() int { return len(c.buckets) }

func (s series) point(name string, value float64, attrs string) Point {
	return Point{
		Timestamp:   s.minute,
		TenantID:    s.tenantID,
		ProjectID:   s.projectID,
		MetricName:  name,
		Value:       value,
		ServiceName: s.service,
		Attrs:       attrs,
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
