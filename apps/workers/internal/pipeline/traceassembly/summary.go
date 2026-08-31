package traceassembly

import (
	"bytes"
	"sort"
	"time"
)

// statusCodeOK is the flattened form of OTLP's STATUS_CODE_OK: a status
// somebody set to success, which is not the same as one nobody set.
const statusCodeOK = "OK"

// The severities `trace_summaries.worst_severity` holds, worst wins. The API
// maps them back to the status names; they are numbers here because a max over
// a number is a merge the store performs for free.
const (
	severityUnset uint8 = 0
	severityOK    uint8 = 1
	severityError uint8 = 2
)

// endpointSeparator joins a service to an operation inside `span_endpoints`.
// A unit separator cannot occur in either: both come from OTLP names, and a
// control character there would already have been rejected upstream.
const endpointSeparator = "\x1f"

// maxTraceEndpoints caps the distinct (service, operation) pairs one trace
// contributes. Measured over a day of the busiest dev project, a trace holds
// p50 2, p99 19 and at most 48 — so this bounds a pathological trace without
// touching a real one, and `EndpointsTruncated` says when it ever bites.
const maxTraceEndpoints = 256

// summaryKey identifies one trace. It carries the tenancy because a trace id
// does not identify a trace on its own: W3C trace context propagates between
// organisations, so two customers can export the same id (see sample.Facts).
type summaryKey struct {
	tenantID  string
	projectID string
	traceID   string
}

// SummaryStats are the accumulator's running totals, for the heartbeat and for
// tests.
type SummaryStats struct {
	OpenTraces int
	// Written counts rows handed to the store, Revised the subset of those that
	// described a trace whose row had already been written once — a trace that
	// gained a span after the sampler released it.
	Written uint64
	Revised uint64
	// Evicted counts traces forgotten because the accumulator was full rather
	// than because their retention ran out. A number that climbs means a late
	// span can no longer find its trace, so the correction it would have made
	// becomes a second row instead.
	Evicted uint64
	// EndpointsTruncated counts traces that reached maxTraceEndpoints, whose
	// operation filter is therefore answered from a partial set.
	EndpointsTruncated uint64
}

// traceSummary is one trace as the rows stored for it describe it.
//
// It is a *complete* picture rather than a delta, which is what lets every
// column of `trace_summaries` merge with max, min or set union and no sum: a
// row emitted twice, or re-emitted after a late span, reduces to the same
// answer. That is the whole idempotency argument for this table.
type traceSummary struct {
	retentionDays uint16
	earliestStart time.Time
	latestEnd     time.Time
	// receivedAt is our own clock, and the earliest we saw any span of this
	// trace: the summary must not outlive the spans it points at, and both
	// tables partition and expire on this.
	receivedAt time.Time

	rootService string
	rootName    string
	// rootRank is what argMin over (has_parent, start) does in SQL: a span with
	// no parent beats one with a parent, and among equals the earliest starts.
	rootRank rootRank

	spanCount     uint32
	errorCount    uint32
	worstSeverity uint8

	services  map[string]struct{}
	endpoints map[string]struct{}

	// dirty is set by every span and cleared by the drain that writes the row,
	// so a trace that has not changed is not written again.
	dirty     bool
	forgetAt  time.Time
	everWrote bool
}

// rootRank orders candidate root spans. Comparable, so it is one `less` call.
type rootRank struct {
	hasParent bool
	start     time.Time
}

func (r rootRank) before(other rootRank) bool {
	if r.hasParent != other.hasParent {
		return !r.hasParent
	}
	return r.start.Before(other.start)
}

// summaryRow is one row of `trace_summaries`, ready for the block.
type summaryRow struct {
	TenantID      string
	ProjectID     string
	StartTime     time.Time
	TraceID       []byte
	ReceivedAt    time.Time
	RetentionDays uint16
	DurationMS    float64
	SpanCount     uint32
	ErrorCount    uint32
	WorstSeverity uint8
	RootService   string
	RootName      string
	Services      []string
	Endpoints     []string
}

// summaries accumulates one row of `trace_summaries` per stored trace.
//
// It sees the rows the sampler released — the spans that reach `spans` — and
// nothing else, which is the point: a trace listed here can be opened, where a
// summary derived before the sampler would name traces whose spans were never
// stored. That is a different choice from the counters and the edge rollup
// beside it, and a deliberate one: those are metrics and this is an index.
type summaries struct {
	// retention is how long a trace's state is kept after its row is written,
	// so a span arriving late enough to take the sampler's fast path is folded
	// into the row that already exists instead of starting a second one. It is
	// the sampler's own DecisionTTL for exactly that reason: past it the buffer
	// has forgotten the trace too and judges the late span as a new one.
	retention time.Duration
	maxTraces int

	open  map[summaryKey]*traceSummary
	order queue[summaryKey]
	// dirty is how many held traces have changed since the last drain, so a
	// poll that released nothing costs a queue peek rather than a map scan.
	dirty int
	stats SummaryStats
}

func newSummaries(retention time.Duration, maxTraces int) *summaries {
	return &summaries{
		retention: retention,
		maxTraces: maxTraces,
		open:      make(map[summaryKey]*traceSummary),
	}
}

// Observe folds one stored span into its trace's summary.
func (s *summaries) Observe(now time.Time, row spanRow) {
	key := summaryKey{
		tenantID:  row.TenantID,
		projectID: row.ProjectID,
		traceID:   row.traceKey(),
	}
	trace, held := s.open[key]
	if !held {
		trace = newTraceSummary(now.Add(s.retention), row)
		s.open[key] = trace
		s.order.push(key)
	}
	if !trace.dirty {
		trace.dirty = true
		s.dirty++
	}
	trace.observe(row, &s.stats)
}

// newTraceSummary starts a trace's state. `forgetAt` is fixed here and never
// refreshed, which is what keeps the eviction queue in expiry order — the same
// property `sample.openTrace.closesAt` relies on.
func newTraceSummary(forgetAt time.Time, row spanRow) *traceSummary {
	return &traceSummary{
		retentionDays: row.RetentionDays,
		earliestStart: row.Timestamp,
		receivedAt:    row.ReceivedAt,
		rootRank:      rootRank{hasParent: true, start: row.Timestamp.Add(time.Nanosecond)},
		services:      make(map[string]struct{}),
		endpoints:     make(map[string]struct{}),
		forgetAt:      forgetAt,
	}
}

func (t *traceSummary) observe(row spanRow, stats *SummaryStats) {
	t.spanCount++
	if row.Timestamp.Before(t.earliestStart) {
		t.earliestStart = row.Timestamp
	}
	if end := row.Timestamp.Add(millisecondsDuration(row.DurationMS)); end.After(t.latestEnd) {
		t.latestEnd = end
	}
	if row.ReceivedAt.Before(t.receivedAt) {
		t.receivedAt = row.ReceivedAt
	}

	severity := spanSeverity(row)
	if severity > t.worstSeverity {
		t.worstSeverity = severity
	}
	if severity == severityError {
		t.errorCount++
	}

	if rank := (rootRank{hasParent: hasParent(row), start: row.Timestamp}); rank.before(t.rootRank) {
		t.rootRank = rank
		t.rootService, t.rootName = row.ServiceName, row.SpanName
	}

	t.services[row.ServiceName] = struct{}{}
	t.addEndpoint(row, stats)
}

// addEndpoint records the (service, operation) pair this span makes filterable.
// The pair travels as one value because a span filter matches when ONE span
// satisfies every part of it together — two independent lists would match a
// trace whose service and whose operation are on different spans.
func (t *traceSummary) addEndpoint(row spanRow, stats *SummaryStats) {
	endpoint := row.ServiceName + endpointSeparator + row.SpanName
	if _, known := t.endpoints[endpoint]; known {
		return
	}
	if len(t.endpoints) >= maxTraceEndpoints {
		stats.EndpointsTruncated++
		return
	}
	t.endpoints[endpoint] = struct{}{}
}

// spanSeverity is the span's status as the summary's worst-status column holds
// it. The worker strips OTLP's `STATUS_CODE_` prefix before a row is built, so
// these are the stored spellings.
func spanSeverity(row spanRow) uint8 {
	switch row.StatusCode {
	case statusCodeError:
		return severityError
	case statusCodeOK:
		return severityOK
	default:
		return severityUnset
	}
}

// hasParent reports whether this span names a parent. An absent one is written
// as sixteen NULs, and an SDK that sends eight zero bytes renders as sixteen
// ASCII zeroes; both mean root, and the read side tests for both too.
func hasParent(row spanRow) bool {
	for _, b := range row.ParentSpanID {
		if b != 0 && b != '0' {
			return true
		}
	}
	return false
}

// Drain returns a complete row for every trace that gained a span since the
// last drain, and forgets the traces whose retention has run out.
//
// Rows are sorted so the same traces drained twice produce the same block,
// which is what lets the store's own block deduplication recognise a retried
// insert as the repeat it is.
func (s *summaries) Drain(now time.Time) []summaryRow {
	if s.dirty == 0 {
		s.retire(now)
		return nil
	}
	rows := make([]summaryRow, 0, s.dirty)
	s.dirty = 0
	for key, trace := range s.open {
		if trace.dirty {
			rows = append(rows, trace.row(key))
			trace.dirty = false
			s.stats.Written++
			if trace.everWrote {
				s.stats.Revised++
			}
			trace.everWrote = true
		}
	}
	s.retire(now)
	sortSummaryRows(rows)
	return rows
}

// retire forgets the traces no late span can still reach, then the oldest ones
// beyond the cap. It runs only from a drain, when every held trace has already
// been written: dropping one costs the chance to revise its row and never the
// row itself. Evicting a trace whose row had not been written would leave a
// stored trace the list cannot show, which is the one outcome to avoid.
func (s *summaries) retire(now time.Time) {
	s.forgetExpired(now)
	s.evictOverflow()
}

// DrainAll writes every trace held, whether or not it has changed, and forgets
// all of them. Shutdown calls it: a summary never written is a stored trace the
// list cannot show.
func (s *summaries) DrainAll() []summaryRow {
	rows := make([]summaryRow, 0, len(s.open))
	for key, trace := range s.open {
		rows = append(rows, trace.row(key))
		s.stats.Written++
	}
	s.open = make(map[summaryKey]*traceSummary)
	s.order.reset()
	s.dirty = 0
	sortSummaryRows(rows)
	return rows
}

func (t *traceSummary) row(key summaryKey) summaryRow {
	return summaryRow{
		TenantID:      key.tenantID,
		ProjectID:     key.projectID,
		StartTime:     t.earliestStart,
		TraceID:       []byte(key.traceID),
		ReceivedAt:    t.receivedAt,
		RetentionDays: t.retentionDays,
		DurationMS:    t.durationMS(),
		SpanCount:     t.spanCount,
		ErrorCount:    t.errorCount,
		WorstSeverity: t.worstSeverity,
		RootService:   t.rootService,
		RootName:      t.rootName,
		Services:      sortedKeys(t.services),
		Endpoints:     sortedKeys(t.endpoints),
	}
}

// durationMS is the whole trace, earliest start to latest end — not the root
// span's own duration, which would call a trace fast when its root returned
// before the work it started had finished.
func (t *traceSummary) durationMS() float64 {
	if t.latestEnd.Before(t.earliestStart) {
		return 0
	}
	return float64(t.latestEnd.Sub(t.earliestStart)) / nanosPerMillisecond
}

// forgetExpired drops the traces no late span can still reach.
func (s *summaries) forgetExpired(now time.Time) {
	for {
		key, ok := s.order.peek()
		if !ok {
			return
		}
		trace, held := s.open[key]
		if !held {
			s.order.pop()
			continue
		}
		if now.Before(trace.forgetAt) {
			return
		}
		s.order.pop()
		delete(s.open, key)
	}
}

// evictOverflow forgets the oldest traces until the map is inside its cap. An
// unbounded map on the ingest path is an outage waiting for a traffic spike.
// Between drains the map can exceed the cap, bounded by what the sampling
// buffer can release at once — which its own MaxTraces and MaxSpans limit.
func (s *summaries) evictOverflow() {
	for len(s.open) > s.maxTraces {
		key, ok := s.order.pop()
		if !ok {
			return
		}
		if _, held := s.open[key]; !held {
			continue
		}
		delete(s.open, key)
		s.stats.Evicted++
	}
}

// Stats returns the running totals plus what is held right now.
func (s *summaries) Stats() SummaryStats {
	current := s.stats
	current.OpenTraces = len(s.open)
	return current
}

// sortSummaryRows puts a drain in one order, so the same rows re-derived make
// the same block and the store recognises a retry.
func sortSummaryRows(rows []summaryRow) {
	sort.Slice(rows, func(i, j int) bool {
		left, right := rows[i], rows[j]
		if left.TenantID != right.TenantID {
			return left.TenantID < right.TenantID
		}
		if left.ProjectID != right.ProjectID {
			return left.ProjectID < right.ProjectID
		}
		if !left.StartTime.Equal(right.StartTime) {
			return left.StartTime.Before(right.StartTime)
		}
		return bytes.Compare(left.TraceID, right.TraceID) < 0
	})
}

func sortedKeys(set map[string]struct{}) []string {
	keys := make([]string, 0, len(set))
	for key := range set {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// queue is a first-in-first-out list of trace keys, so the oldest entry is
// evicted first. `sample` holds an identical one for the same reason and keeps
// it unexported to its own package; this is a fifteen-line structure, and
// widening either package's API to share it would couple the sampler to the
// summary for no gain.
//
// Popped slots are compacted rather than shifted on every pop, so a steady
// stream costs amortised constant time per entry.
type queue[T any] struct {
	items []T
	head  int
}

// compactAfter is how many popped slots accumulate before the queue reclaims
// them, chosen so compaction is rare and the wasted slots stay bounded.
const compactAfter = 1024

func (q *queue[T]) push(item T) { q.items = append(q.items, item) }

func (q *queue[T]) peek() (T, bool) {
	if q.head >= len(q.items) {
		var zero T
		return zero, false
	}
	return q.items[q.head], true
}

func (q *queue[T]) pop() (T, bool) {
	item, ok := q.peek()
	if !ok {
		return item, false
	}
	var zero T
	q.items[q.head] = zero // release the reference; the slot outlives the pop
	q.head++
	if q.head >= compactAfter {
		q.items = append(q.items[:0], q.items[q.head:]...)
		q.head = 0
	}
	return item, true
}

func (q *queue[T]) reset() {
	q.items = q.items[:0]
	q.head = 0
}
