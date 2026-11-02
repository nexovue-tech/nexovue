package derive

import (
	"sort"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/latencysample"
)

// Span kinds, spelled as the flattened span row spells them: the OTLP enum name
// with its `SPAN_KIND_` prefix removed.
const (
	KindServer   = "SERVER"
	KindConsumer = "CONSUMER"
)

// EndpointStat is one row of `endpoint_stats_1m`: what one endpoint of one
// service did in one minute, counted over every span rather than the sampled
// ones.
type EndpointStat struct {
	Minute        time.Time
	TenantID      string
	ProjectID     string
	RetentionDays uint16
	ServiceName   string
	SpanKind      string
	SpanName      string
	RequestCount  uint64
	ErrorCount    uint64
	// LatencyMS are the durations behind this row, sorted ascending and capped.
	// The store folds them into the row's quantile state; no client builds an
	// AggregateFunction, so the serialised form stays the server's.
	LatencyMS []float64
}

// endpointKey identifies one row of `endpoint_stats_1m`. It is the table's own
// sort key, so a bucket and a row are the same thing, and it is a comparable
// struct so it can key a map without formatting a string per span.
type endpointKey struct {
	minute      time.Time
	tenantID    string
	projectID   string
	serviceName string
	spanKind    string
	spanName    string
	// Constant for a given project, but carried here because it sits in the
	// target table's partition key.
	retentionDays uint16
}

// countedEndpoint is one partition's share of one endpoint-minute, for the same
// reason `counted` is: a revoke drops a partition's contribution, and the shares
// are summed back together at drain time.
type countedEndpoint struct {
	endpointKey
	partition int32
}

// before orders shares so that the ones belonging to an endpoint-minute are
// adjacent and in ascending partition order, which is what makes a drain
// reproducible.
func (c countedEndpoint) before(other countedEndpoint) bool {
	switch {
	case !c.minute.Equal(other.minute):
		return c.minute.Before(other.minute)
	case c.tenantID != other.tenantID:
		return c.tenantID < other.tenantID
	case c.projectID != other.projectID:
		return c.projectID < other.projectID
	case c.serviceName != other.serviceName:
		return c.serviceName < other.serviceName
	case c.spanKind != other.spanKind:
		return c.spanKind < other.spanKind
	case c.spanName != other.spanName:
		return c.spanName < other.spanName
	case c.retentionDays != other.retentionDays:
		return c.retentionDays < other.retentionDays
	default:
		return c.partition < other.partition
	}
}

type endpointTotals struct {
	requests uint64
	errors   uint64
	latency  latencysample.Reservoir
}

// add folds one partition's share into a running total for its endpoint-minute.
func (t *endpointTotals) add(share *endpointTotals) {
	t.requests += share.requests
	t.errors += share.errors
	t.latency.Merge(share.latency)
}

// inbound reports whether a span is a request this service served. The rollup
// answers "how is each service doing", so it holds the two kinds that mean
// somebody called us — a CLIENT span is our call to someone else and is already
// counted as their SERVER span, or as an edge.
func inbound(spanKind string) bool {
	return spanKind == KindServer || spanKind == KindConsumer
}

// observeEndpoint folds one span into its endpoint-minute, and into its
// service-minute. minute is the bucket Observe already decided, so the rollups
// and the counters beside them cannot disagree about which clock the span was
// bucketed on.
func (c *Counters) observeEndpoint(minute time.Time, span Span) {
	if !inbound(span.SpanKind) {
		return
	}
	c.observeService(minute, span)
	key := countedEndpoint{
		endpointKey: endpointKey{
			minute:        minute,
			tenantID:      span.TenantID,
			projectID:     span.ProjectID,
			serviceName:   span.ServiceName,
			spanKind:      span.SpanKind,
			spanName:      span.SpanName,
			retentionDays: span.RetentionDays,
		},
		partition: span.Partition,
	}
	totals, ok := c.endpoints[key]
	if !ok {
		totals = &endpointTotals{}
		c.endpoints[key] = totals
	}
	totals.requests++
	if span.Errored {
		totals.errors++
	}
	totals.latency.Observe(latencysample.Weigh(span.TraceID, span.SpanID), span.DurationMS)
}

// drainEndpoints hands out the rows for every minute the caller calls closed,
// removing them from memory.
func (c *Counters) drainEndpoints(closed func(minute time.Time) bool) []EndpointStat {
	due := make([]countedEndpoint, 0, len(c.endpoints))
	for key := range c.endpoints {
		if closed(key.minute) {
			due = append(due, key)
		}
	}
	sort.Slice(due, func(i, j int) bool { return due[i].before(due[j]) })

	stats := make([]EndpointStat, 0, len(due))
	var (
		row     endpointKey
		running endpointTotals
		open    bool
	)
	for _, key := range due {
		if open && key.endpointKey != row {
			stats = append(stats, row.stat(running))
			running = endpointTotals{}
		}
		row, open = key.endpointKey, true
		running.add(c.endpoints[key])
		delete(c.endpoints, key)
	}
	if open {
		stats = append(stats, row.stat(running))
	}

	// Sorted for the same reason the counter points are: the block ClickHouse
	// receives must be byte-identical when a restart re-derives the same minute
	// from the same offsets, or its insert deduplication cannot recognise the
	// retry and the rollup double-counts.
	sort.Slice(stats, func(i, j int) bool { return stats[i].less(stats[j]) })
	return stats
}

// stat is the row one endpoint-minute's summed totals become.
func (k endpointKey) stat(summed endpointTotals) EndpointStat {
	return EndpointStat{
		Minute:        k.minute,
		TenantID:      k.tenantID,
		ProjectID:     k.projectID,
		RetentionDays: k.retentionDays,
		ServiceName:   k.serviceName,
		SpanKind:      k.spanKind,
		SpanName:      k.spanName,
		RequestCount:  summed.requests,
		ErrorCount:    summed.errors,
		LatencyMS:     summed.latency.Values(),
	}
}

// less orders endpoint rows totally, so a drain is reproducible. The fields are
// the whole key: any pair left tied would order at the map's whim.
func (s EndpointStat) less(other EndpointStat) bool {
	switch {
	case !s.Minute.Equal(other.Minute):
		return s.Minute.Before(other.Minute)
	case s.TenantID != other.TenantID:
		return s.TenantID < other.TenantID
	case s.ProjectID != other.ProjectID:
		return s.ProjectID < other.ProjectID
	case s.ServiceName != other.ServiceName:
		return s.ServiceName < other.ServiceName
	case s.SpanKind != other.SpanKind:
		return s.SpanKind < other.SpanKind
	case s.SpanName != other.SpanName:
		return s.SpanName < other.SpanName
	default:
		return s.RetentionDays < other.RetentionDays
	}
}
