// Package serviceedges derives the service dependency graph — one row of
// `service_edges_1m` per caller→callee pair per minute — from spans.
//
// An edge is a CLIENT span answered by a SERVER span, or a PRODUCER whose
// message reached a CONSUMER. A call's two spans are parent and child, which is
// the pairing the OpenTelemetry service graph connector uses. A queue hop's are
// not: messaging instrumentation starts the consumer in its own trace and
// *links* back to the producer, because a span has one parent and a batch
// consume has many producers — so a consumer's producer is found in its links.
// Pairing on the parent alone is why this package had never produced a queue
// edge. Spans arrive in either order, so each side waits a bounded time for its
// partner.
//
// A queue hop crosses traces, and the receiver keys Kafka by trace id, so the
// producer and the consumer can land on different partitions. They pair only
// while one worker process holds both — with the pipeline replicated, the
// fraction of queue edges that span two workers is lost. A call never has this
// problem: both its spans are in one trace, on one partition.
//
// It runs before the sampler, over every span of every trace, for the same
// reason the counters do (ADR 0010): tail sampling keeps roughly one healthy
// trace in ten, so a graph measured after it would report a tenth of the real
// call rate — and a dependency that only ever succeeds would look like a tenth
// of itself while one that always errors looked whole.
//
// The package owns no I/O and no clock. It is not safe for concurrent use: one
// pipeline goroutine owns it, which is how the trace-assembly loop is built.
package serviceedges

import (
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/clocktrust"
)

// Span kinds, spelled as the flattened span row spells them: the OTLP enum name
// with its `SPAN_KIND_` prefix removed.
const (
	KindClient   = "CLIENT"
	KindServer   = "SERVER"
	KindProducer = "PRODUCER"
	KindConsumer = "CONSUMER"
)

// Edge kinds, the values written to `edge_kind`. A queue hop and a direct call
// are different dependencies even between the same two services — one can be
// retried for hours, the other fails the caller now — so they never share a row.
const (
	EdgeCall  = "call"
	EdgeQueue = "queue"
)

// UninstrumentedService is the callee written for a CLIENT span whose SERVER
// partner never arrived. The call is evidence: something answered it and the
// caller waited. Naming the callee would be an invention, so the edge points at
// a reserved name instead — a real `service.name` is a resource attribute and
// does not carry parentheses.
const UninstrumentedService = "(uninstrumented)"

// UnknownService is the caller written for a service whose spans carry no
// `service.name`. The OpenTelemetry SDKs default that attribute to
// `unknown_service`, so an empty one means the resource was assembled by hand
// and left it out — a fact about the sender, not a service, and folding it into
// one bucket keeps it visible instead of dropping its dependencies.
const UnknownService = "(unknown)"

// The bucket width and the clock-trust bounds come from clocktrust, so edges
// and counters cannot disagree about which clock a row is bucketed on.

// Span is one span as edge derivation sees it. The row type it is mapped from
// is deliberately not imported: this package pairs spans, and knowing nothing
// about the storage row is what keeps the two free to change apart.
type Span struct {
	TenantID  string
	ProjectID string
	// RetentionDays is the project's derived-signal retention. It reaches the
	// edge row because it sits in the target table's partition key, so a
	// retention class expires as whole parts.
	RetentionDays uint16

	// TraceID scopes SpanID. Span ids are only unique within a trace, and an
	// id that repeats — a broken SDK emitting a constant, a zeroed field — would
	// otherwise pair spans that have nothing to do with each other.
	TraceID []byte
	// SpanID and ParentSpanID are the span's own id and its parent's. A span
	// with no usable SpanID cannot be paired from either side and takes no part
	// in the graph.
	SpanID       []byte
	ParentSpanID []byte
	// Links are the spans this one names as its causes. A CONSUMER's producer
	// is here and nowhere else: messaging instrumentation starts the consumer in
	// its own trace and links back, because a span has one parent and a batch
	// consume has many producers.
	Links []SpanLink

	// ServiceName is `service.name` from the span's resource, already resolved.
	ServiceName string
	// Kind is one of the Kind* constants. Anything else is not an edge.
	Kind string

	// Timestamp is when the client says the span started; ReceivedAt is when we
	// took the message carrying it, which is the only clock in the path we
	// control and so what Timestamp is judged against.
	Timestamp  time.Time
	ReceivedAt time.Time

	// DurationMS is the span's own duration. On the caller's span that is what
	// the caller experienced, network included, which is what the edge records.
	DurationMS float64
	Errored    bool
}

// SpanLink is one span a span links to: the identity of a cause, in the trace
// that cause belongs to.
type SpanLink struct {
	TraceID []byte
	SpanID  []byte
}

// usableID reports whether an id names anything.
//
// Ids reach this package hex-encoded into fixed-width columns, so an absent one
// is not an empty slice: it is the column's width in NUL bytes, and an
// all-zeroes id encodes to the same width in ASCII zeroes. Both mean "no span",
// and a length check alone would take either for a real id — which is why a
// root span used to park in the buffer waiting for a parent that cannot exist.
func usableID(id []byte) bool {
	for _, b := range id {
		if b != 0x00 && b != '0' {
			return true
		}
	}
	return false
}

// Edge is one row of `service_edges_1m`.
type Edge struct {
	Minute        time.Time
	TenantID      string
	ProjectID     string
	RetentionDays uint16
	ClientService string
	ServerService string
	Kind          string
	CallCount     uint64
	ErrorCount    uint64
	// LatencyMS are client-side durations, sorted ascending and capped. The
	// store turns them into the row's quantile state.
	LatencyMS []float64
}

// callerKind reports the edge kind a caller-side span opens, and whether it is
// one at all.
func callerKind(spanKind string) (edgeKind string, isCaller bool) {
	switch spanKind {
	case KindClient:
		return EdgeCall, true
	case KindProducer:
		return EdgeQueue, true
	}
	return "", false
}

// calleeKind reports the edge kind a callee-side span closes, and whether it is
// one at all.
func calleeKind(spanKind string) (edgeKind string, isCallee bool) {
	switch spanKind {
	case KindServer:
		return EdgeCall, true
	case KindConsumer:
		return EdgeQueue, true
	}
	return "", false
}

// serviceOf names the service a span belongs to, folding a missing name into a
// single reserved bucket rather than an empty string that reads as a real value.
func serviceOf(span Span) string {
	if span.ServiceName == "" {
		return UnknownService
	}
	return span.ServiceName
}

func startOfMinute(at time.Time) time.Time { return at.UTC().Truncate(clocktrust.Bucket) }

func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}
