// Package serviceedges derives the service dependency graph — one row of
// `service_edges_1m` per caller→callee pair per minute — from spans.
//
// An edge is a CLIENT span whose child is a SERVER span, or a PRODUCER whose
// child is a CONSUMER. That is the pairing the OpenTelemetry service graph
// connector uses, and it is the only evidence a span carries about who called
// whom: the callee's span names the callee, the caller's parent link names the
// caller. Spans arrive in either order, so each side waits a bounded time for
// its partner.
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

import "time"

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

// bucketWidth is the resolution of the rollup, matching `derive`: the finest a
// dashboard reads and the coarsest that still shows a spike.
const bucketWidth = time.Minute

// How far a client's own clock may put a span from the moment we received it
// before we stop bucketing on it. Same rule and same reasoning as
// `derive.bucketInstant`; both would be better served by one shared clock-trust
// package, which is a change to `derive` and so not made here.
const (
	// trustedFutureSkew is one bucket width: a bucket further ahead than the
	// bucketing itself costs would add lag for nothing, and a bucket far enough
	// ahead never settles at all.
	trustedFutureSkew = bucketWidth

	// trustedPastSkew is the broker's own retention: nothing legitimately
	// delivered can be older than the buffer it was replayed from.
	trustedPastSkew = 7 * 24 * time.Hour
)

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
	// SpanID and ParentSpanID are the raw 8-byte ids. A span with no SpanID
	// cannot be paired from either side and takes no part in the graph.
	SpanID       []byte
	ParentSpanID []byte

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

// bucketInstant is the moment a span is counted at, and the skew discarded to
// get there.
//
// A timestamp outside the trusted window is attributed to our own clock rather
// than clamped or rejected: clamping still leaves a bucket in the future, which
// is the wedge itself only smaller, and rejecting costs a count that the
// pre-sampling invariant says must be exact.
//
// A span with no ReceivedAt is judged against the moment it is being processed.
// Leaving it unjudged instead — as an absent receive time invites — would let a
// wrong client clock open a bucket arbitrarily far ahead, and a bucket that
// never settles stops offsets committing for good.
func bucketInstant(span Span, processingAt time.Time) (at time.Time, discarded time.Duration) {
	reference := span.ReceivedAt
	if reference.IsZero() {
		reference = processingAt
	}
	skew := span.Timestamp.Sub(reference)
	if skew > trustedFutureSkew || skew < -trustedPastSkew {
		return reference, skew
	}
	return span.Timestamp, 0
}

func startOfMinute(at time.Time) time.Time { return at.UTC().Truncate(bucketWidth) }

func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}
