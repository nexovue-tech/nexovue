package traceassembly

import (
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

const (
	testRetention = time.Minute
	testMaxTraces = 1000
)

var summaryStart = time.Unix(0, 1_700_000_000_000_000_000).UTC()

// summarySpan is one stored span of a named trace. The defaults are a healthy
// child span; each test changes only what it is about.
func summarySpan(traceID string, options ...func(*spanRow)) spanRow {
	row := testRow()
	row.TraceID = otlpvalue.HexID([]byte(traceID), otlpvalue.TraceIDHexLen)
	row.ParentSpanID = []byte("00000000000000ab")
	row.Timestamp = summaryStart
	row.RetentionDays = 30
	row.ReceivedAt = summaryStart.Add(time.Second)
	for _, option := range options {
		option(&row)
	}
	return row
}

func at(offset time.Duration) func(*spanRow) {
	return func(row *spanRow) { row.Timestamp = summaryStart.Add(offset) }
}

func lasting(ms float64) func(*spanRow) {
	return func(row *spanRow) { row.DurationMS = ms }
}

func status(code string) func(*spanRow) {
	return func(row *spanRow) { row.StatusCode = code }
}

func named(service, operation string) func(*spanRow) {
	return func(row *spanRow) { row.ServiceName, row.SpanName = service, operation }
}

func rootSpan(row *spanRow) {
	row.ParentSpanID = otlpvalue.HexID(nil, otlpvalue.SpanIDHexLen)
}

func drainOne(t *testing.T, accumulator *summaries, now time.Time) summaryRow {
	t.Helper()
	rows := accumulator.Drain(now)
	if len(rows) != 1 {
		t.Fatalf("drained %d rows, want 1", len(rows))
	}
	return rows[0]
}

// The row has to describe the whole trace, because the list renders it without
// touching `spans` at all.
func TestASummaryDescribesTheWholeTraceNotOneSpan(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	now := summaryStart

	accumulator.Observe(now, summarySpan("trace-1", rootSpan, named("storefront", "POST /checkout"), lasting(40)))
	accumulator.Observe(now, summarySpan("trace-1", at(10*time.Millisecond), named("payments", "charge"), lasting(15), status("ERROR")))
	accumulator.Observe(now, summarySpan("trace-1", at(5*time.Millisecond), named("payments", "authorize"), lasting(2)))

	row := drainOne(t, accumulator, now)

	if row.SpanCount != 3 {
		t.Errorf("SpanCount = %d, want 3", row.SpanCount)
	}
	if row.ErrorCount != 1 {
		t.Errorf("ErrorCount = %d, want 1", row.ErrorCount)
	}
	if row.WorstSeverity != severityError {
		t.Errorf("WorstSeverity = %d, want %d", row.WorstSeverity, severityError)
	}
	if !row.StartTime.Equal(summaryStart) {
		t.Errorf("StartTime = %s, want the earliest span's %s", row.StartTime, summaryStart)
	}
	if row.DurationMS != 40 {
		t.Errorf("DurationMS = %v, want 40 — earliest start to latest end", row.DurationMS)
	}
	if row.RootService != "storefront" || row.RootName != "POST /checkout" {
		t.Errorf("root = %s/%s, want storefront/POST /checkout", row.RootService, row.RootName)
	}
}

// A root that returns before the work it started has finished must not make the
// trace look fast: the duration is earliest start to latest end.
func TestDurationCoversTheSlowestChildNotTheRoot(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", rootSpan, lasting(5)))
	accumulator.Observe(summaryStart, summarySpan("trace-1", at(2*time.Millisecond), lasting(90)))

	if row := drainOne(t, accumulator, summaryStart); row.DurationMS != 92 {
		t.Errorf("DurationMS = %v, want 92", row.DurationMS)
	}
}

// A trace whose root never arrived still has to render, so the earliest span
// names it — which is what argMin over (has_parent, start) does on the read.
func TestAPartialTraceIsNamedByItsEarliestSpan(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", at(9*time.Millisecond), named("payments", "charge")))
	accumulator.Observe(summaryStart, summarySpan("trace-1", named("cart", "cart.query")))

	row := drainOne(t, accumulator, summaryStart)
	if row.RootService != "cart" || row.RootName != "cart.query" {
		t.Errorf("root = %s/%s, want cart/cart.query", row.RootService, row.RootName)
	}
}

// A parentless span wins however late it starts, or a trace would be named
// after whichever child happened to begin first.
func TestARootBeatsAnEarlierChild(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", named("payments", "charge")))
	accumulator.Observe(summaryStart, summarySpan("trace-1", rootSpan, at(9*time.Millisecond), named("storefront", "POST /checkout")))

	row := drainOne(t, accumulator, summaryStart)
	if row.RootService != "storefront" {
		t.Errorf("RootService = %s, want storefront", row.RootService)
	}
}

// The service and the operation travel as one value because a span filter
// matches when ONE span satisfies all of it together. Two independent lists
// would match a trace whose service and whose operation are on different spans.
func TestEndpointsPairTheServiceWithTheOperation(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", named("cart", "cart.query")))
	accumulator.Observe(summaryStart, summarySpan("trace-1", named("payments", "charge")))
	accumulator.Observe(summaryStart, summarySpan("trace-1", named("payments", "charge")))

	row := drainOne(t, accumulator, summaryStart)

	wantServices := []string{"cart", "payments"}
	if !slices.Equal(row.Services, wantServices) {
		t.Errorf("Services = %v, want %v", row.Services, wantServices)
	}
	wantEndpoints := []string{
		"cart" + endpointSeparator + "cart.query",
		"payments" + endpointSeparator + "charge",
	}
	if !slices.Equal(row.Endpoints, wantEndpoints) {
		t.Errorf("Endpoints = %v, want %v", row.Endpoints, wantEndpoints)
	}
	if slices.Contains(row.Endpoints, "cart"+endpointSeparator+"charge") {
		t.Error("a pair was assembled across two spans")
	}
}

// The whole idempotency argument for `trace_summaries`: a re-emitted row is a
// complete snapshot, so max, min and set union merge it to the same answer and
// the table needs no deduplication token and no watermark of its own.
func TestALateSpanReEmitsTheWholeTraceNotADelta(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", rootSpan, lasting(10)))
	accumulator.Observe(summaryStart, summarySpan("trace-1", lasting(10)))
	first := drainOne(t, accumulator, summaryStart)

	late := summaryStart.Add(5 * time.Second)
	accumulator.Observe(late, summarySpan("trace-1", at(20*time.Millisecond), lasting(30), status("ERROR")))
	second := drainOne(t, accumulator, late)

	if first.SpanCount != 2 {
		t.Fatalf("first SpanCount = %d, want 2", first.SpanCount)
	}
	if second.SpanCount != 3 {
		t.Errorf("second SpanCount = %d, want 3 — a delta, not a snapshot", second.SpanCount)
	}
	if second.ErrorCount != 1 || second.WorstSeverity != severityError {
		t.Errorf("second errors = %d/%d, want 1/%d", second.ErrorCount, second.WorstSeverity, severityError)
	}
	if second.DurationMS != 50 {
		t.Errorf("second DurationMS = %v, want 50", second.DurationMS)
	}
	// max() over the two rows is the second one, which is the whole point.
	if second.SpanCount < first.SpanCount || second.DurationMS < first.DurationMS {
		t.Error("the revision is not a superset; max() over the two would lose data")
	}
	if got := accumulator.Stats().Revised; got != 1 {
		t.Errorf("Revised = %d, want 1", got)
	}
}

// A root ends last, so it is exported after its children — often after the trace
// was first written. The revision has to land on the row already written: under a
// new key it never merges, and the first row stays listed as a trace of its own.
func TestARootArrivingAfterAWriteRevisesTheSameRow(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	accumulator.Observe(summaryStart, summarySpan("trace-1", at(time.Millisecond), named("cart", "AddItem"), lasting(5)))
	first := drainOne(t, accumulator, summaryStart)

	late := summaryStart.Add(2 * time.Second)
	accumulator.Observe(late, summarySpan("trace-1", rootSpan, named("frontend", "checkout"), lasting(20)))
	second := drainOne(t, accumulator, late)

	if !second.StartTime.Equal(first.StartTime) {
		t.Errorf("the revision moved the row's key from %v to %v", first.StartTime, second.StartTime)
	}
	if !second.TraceStart.Equal(summaryStart) {
		t.Errorf("TraceStart = %v, want the root's start %v", second.TraceStart, summaryStart)
	}
	if second.RootService != "frontend" || second.SpanCount != 2 || second.DurationMS != 20 {
		t.Errorf("revision = %s/%d spans/%vms, want frontend/2/20", second.RootService, second.SpanCount, second.DurationMS)
	}
}

// A trace that has not changed must not be written again: the drain runs on
// every poll, and re-writing every held trace would be an insert per poll of
// everything in memory.
func TestADrainWritesOnlyWhatChanged(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	accumulator.Observe(summaryStart, summarySpan("trace-1"))

	if rows := accumulator.Drain(summaryStart); len(rows) != 1 {
		t.Fatalf("first drain wrote %d rows, want 1", len(rows))
	}
	if rows := accumulator.Drain(summaryStart); rows != nil {
		t.Errorf("second drain wrote %d rows, want none", len(rows))
	}
}

// Past the sampler's own DecisionTTL the buffer has forgotten the trace too, so
// there is nothing left for a late span to revise.
func TestATraceIsForgottenOnceTheSamplerHasForgottenIt(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	accumulator.Observe(summaryStart, summarySpan("trace-1"))
	accumulator.Drain(summaryStart)

	accumulator.Drain(summaryStart.Add(testRetention))
	if held := accumulator.Stats().OpenTraces; held != 0 {
		t.Errorf("OpenTraces = %d after the retention ran out, want 0", held)
	}
}

// A trace still arriving is still held. Forgetting it a retention after its first
// span split every trace longer than that: the next span began a summary of its
// own, and the list showed both.
func TestATraceStillArrivingIsNotForgotten(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	accumulator.Observe(summaryStart, summarySpan("trace-1", rootSpan))
	accumulator.Drain(summaryStart)

	busy := summaryStart.Add(testRetention * 3 / 4)
	accumulator.Observe(busy, summarySpan("trace-1", at(time.Second)))
	first := drainOne(t, accumulator, busy)

	// The worker drains on every poll, so one falls between the two spans.
	accumulator.Drain(summaryStart.Add(testRetention + time.Second))

	late := summaryStart.Add(testRetention * 3 / 2)
	accumulator.Observe(late, summarySpan("trace-1", at(2*time.Second)))
	second := drainOne(t, accumulator, late)

	if second.SpanCount != 3 || !second.StartTime.Equal(first.StartTime) {
		t.Errorf("revision = %d spans at %v, want 3 at %v", second.SpanCount, second.StartTime, first.StartTime)
	}

	accumulator.Drain(late.Add(testRetention))
	if held := accumulator.Stats().OpenTraces; held != 0 {
		t.Errorf("OpenTraces = %d a retention after the last span, want 0", held)
	}
}

// An unbounded map on the ingest path is an outage waiting for a traffic spike.
// What eviction costs is the chance to revise a row, never the row itself — so
// it may only ever drop a trace that has already been written.
func TestTheCapDropsOnlyTracesAlreadyWritten(t *testing.T) {
	accumulator := newSummaries(testRetention, 2)

	for _, id := range []string{"trace-1", "trace-2", "trace-3", "trace-4"} {
		accumulator.Observe(summaryStart, summarySpan(id))
	}
	rows := accumulator.Drain(summaryStart)

	if len(rows) != 4 {
		t.Fatalf("drained %d rows, want 4 — a trace was evicted before it was written", len(rows))
	}
	if held := accumulator.Stats().OpenTraces; held != 2 {
		t.Errorf("OpenTraces = %d, want the cap of 2", held)
	}
	if evicted := accumulator.Stats().Evicted; evicted != 2 {
		t.Errorf("Evicted = %d, want 2", evicted)
	}
}

// A trace id is not unique across tenants — W3C trace context propagates
// between organisations — so one customer's trace must never fold into
// another's, which is the mistake `sample.Facts` documents.
func TestTwoTenantsSharingATraceIdStaySeparate(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)

	mine := summarySpan("trace-1")
	theirs := summarySpan("trace-1")
	theirs.TenantID = "tenant-b"
	accumulator.Observe(summaryStart, mine)
	accumulator.Observe(summaryStart, theirs)

	rows := accumulator.Drain(summaryStart)
	if len(rows) != 2 {
		t.Fatalf("drained %d rows, want 2", len(rows))
	}
	for _, row := range rows {
		if row.SpanCount != 1 {
			t.Errorf("%s holds %d spans, want 1 — the tenants folded together", row.TenantID, row.SpanCount)
		}
	}
}

// The same traces re-derived must make the same block, or the store cannot tell
// a retried insert from new data.
func TestADrainIsOrderedSoARetryMakesTheSameBlock(t *testing.T) {
	build := func(ids []string) []summaryRow {
		accumulator := newSummaries(testRetention, testMaxTraces)
		for _, id := range ids {
			accumulator.Observe(summaryStart, summarySpan(id))
		}
		return accumulator.Drain(summaryStart)
	}

	first := build([]string{"trace-1", "trace-2", "trace-3"})
	second := build([]string{"trace-3", "trace-1", "trace-2"})

	if len(first) != len(second) {
		t.Fatalf("drains differ in length: %d and %d", len(first), len(second))
	}
	for i := range first {
		if string(first[i].TraceID) != string(second[i].TraceID) {
			t.Fatalf("row %d is %q then %q; the drain is not ordered", i, first[i].TraceID, second[i].TraceID)
		}
	}
}

// A shutdown must write everything held, changed or not: a stored trace with no
// summary row is one the list cannot show for as long as its spans are kept.
func TestDrainAllWritesEvenTheUnchanged(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	accumulator.Observe(summaryStart, summarySpan("trace-1"))
	accumulator.Drain(summaryStart)

	rows := accumulator.DrainAll()
	if len(rows) != 1 {
		t.Fatalf("DrainAll wrote %d rows, want 1", len(rows))
	}
	if accumulator.Stats().OpenTraces != 0 {
		t.Error("DrainAll left traces held")
	}
}

// The pair set is bounded, because nothing else bounds it. A trace past the cap
// answers an operation filter from a partial set, and the counter says so.
func TestTheEndpointSetIsCappedAndSaysSo(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	for i := range maxTraceEndpoints + 5 {
		accumulator.Observe(summaryStart, summarySpan("trace-1", named("svc", "op-"+strconv.Itoa(i))))
	}

	row := drainOne(t, accumulator, summaryStart)
	if len(row.Endpoints) != maxTraceEndpoints {
		t.Errorf("Endpoints holds %d, want the cap of %d", len(row.Endpoints), maxTraceEndpoints)
	}
	if truncated := accumulator.Stats().EndpointsTruncated; truncated != 5 {
		t.Errorf("EndpointsTruncated = %d, want 5", truncated)
	}
}

// summaryColumns is every column of `trace_summaries` this writer fills, in the
// order the table declares them (`000028`, and `trace_start` from `000034`). A
// column added to that table which the writer must fill belongs here too.
var summaryColumns = []string{
	"tenant_id", "project_id", "start_time", "trace_start", "trace_id", "received_at", "retention_days",
	"duration_ms", "span_count", "error_count", "worst_severity",
	"root_service", "root_name", "span_services", "span_endpoints",
}

func TestTheSummaryBlockCoversEveryColumnOfTheTable(t *testing.T) {
	block := newSummaryBlock()
	if len(block.columns) != len(summaryColumns) {
		t.Fatalf("block has %d columns, the table has %d", len(block.columns), len(summaryColumns))
	}
	for i, column := range block.columns {
		if column.Name != summaryColumns[i] {
			t.Errorf("column %d is %q, want %q", i, column.Name, summaryColumns[i])
		}
	}

	statement := block.statement()
	if !strings.HasPrefix(statement, `INSERT INTO "trace_summaries"`) {
		t.Errorf("statement = %q, want it to insert into trace_summaries", statement)
	}
	for _, column := range summaryColumns {
		if !strings.Contains(statement, `"`+column+`"`) {
			t.Errorf("statement does not name %q: %s", column, statement)
		}
	}
}

// Every column has to advance together, or the server reads one row's values
// against another row's — the arrays being the easy ones to forget.
func TestAppendingASummaryAdvancesEveryColumnTogether(t *testing.T) {
	accumulator := newSummaries(testRetention, testMaxTraces)
	accumulator.Observe(summaryStart, summarySpan("trace-1"))
	row := drainOne(t, accumulator, summaryStart)

	block := newSummaryBlock()
	for i := range 3 {
		block.append(row)
		if block.rows() != i+1 {
			t.Fatalf("rows() = %d after %d appends", block.rows(), i+1)
		}
		for _, column := range block.columns {
			if got := column.Data.Rows(); got != i+1 {
				t.Fatalf("column %q holds %d rows, want %d", column.Name, got, i+1)
			}
		}
	}

	block.reset()
	if block.rows() != 0 {
		t.Errorf("rows() = %d after reset", block.rows())
	}
}
