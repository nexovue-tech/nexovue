package otlp

import (
	"context"
	"log/slog"
	"sync/atomic"
	"time"

	"google.golang.org/genproto/googleapis/rpc/code"
)

// Reasons an export did not go through, or went through degraded. These are the
// names in the summary line and in the reason field of each event line, so a
// query written against one works against the other.
const (
	reasonUnauthenticated = "unauthenticated"
	reasonThrottled       = "throttled"
	reasonInvalid         = "invalid"
	reasonUnavailable     = "unavailable"
	reasonInternal        = "internal"

	// reasonLimiterDown is not a refusal: the limiter failing open still admits
	// the request. It is counted because losing the protection silently is how
	// the next incident starts.
	reasonLimiterDown = "limiter_unavailable"
)

// refusalReasons and degradationReasons fix the summary's shape: every window
// reports every counter, so a zero is a fact rather than a missing field.
var (
	refusalReasons     = []string{reasonUnauthenticated, reasonThrottled, reasonInvalid, reasonUnavailable, reasonInternal}
	degradationReasons = []string{reasonLimiterDown}
)

// Summary line fields. Names follow the control plane's request log so all three
// services answer one query.
const (
	fieldAcceptedRequests = "accepted_requests"
	fieldAcceptedSpans    = "accepted_spans"
	fieldRefusedRequests  = "refused_requests"
	fieldRefusedSpans     = "refused_spans"
	fieldDurationMillis   = "duration_ms"
	fieldMethod           = "method"
	fieldPath             = "path"
	fieldStatus           = "status"
	fieldSpans            = "spans"
	fieldReason           = "reason"
	fieldError            = "error"

	// refusedPrefix turns a reason into its summary field, so the two names
	// cannot drift apart.
	refusedPrefix = "refused_"
)

// Log messages. Fixed strings, because a grep for one of them has to find every
// occurrence and not a formatted variant.
const (
	summaryMessage  = "ingest summary"
	acceptedMessage = "export accepted"
	refusedMessage  = "export refused"
)

// Traffic counts what the door did since the last summary, and is the reason
// the receiver can be seen working at all. A data plane cannot log a line per
// request at INFO — unreadable at ingest rates — but a receiver that logs
// nothing is indistinguishable from one that is silently broken.
//
// Event lines are throttled to the first of each reason per window for the same
// reason: a broker outage refuses every request, and a line per refusal would
// turn one incident into two.
type Traffic struct {
	acceptedRequests atomic.Int64
	acceptedSpans    atomic.Int64
	refusedRequests  atomic.Int64
	refusedSpans     atomic.Int64

	// Keys are fixed at construction, so concurrent writers only touch the
	// counters, never the map.
	tallies map[string]*tally
}

// tally is one reason's count for the window, plus whether it has already been
// logged in this one.
type tally struct {
	count  atomic.Int64
	logged atomic.Bool
}

// NewTraffic returns an empty tally with every reason already present.
func NewTraffic() *Traffic {
	traffic := &Traffic{tallies: make(map[string]*tally)}
	for _, reason := range refusalReasons {
		traffic.tallies[reason] = &tally{}
	}
	for _, reason := range degradationReasons {
		traffic.tallies[reason] = &tally{}
	}
	return traffic
}

// Accepted records an export the door published.
func (t *Traffic) Accepted(spans int) {
	t.acceptedRequests.Add(1)
	t.acceptedSpans.Add(int64(spans))
}

// Refused records an export the door turned away, reporting whether this is the
// first of its reason in the current window and therefore worth a log line.
func (t *Traffic) Refused(reason string, spans int) bool {
	t.refusedRequests.Add(1)
	t.refusedSpans.Add(int64(spans))
	return t.note(reason)
}

// Degraded records a gate that carried on with less protection than it should
// have, reporting whether it is worth a log line this window.
func (t *Traffic) Degraded(reason string) bool { return t.note(reason) }

func (t *Traffic) note(reason string) bool {
	counted, known := t.tallies[reason]
	if !known {
		// A reason the tables above do not list is our bug; say so rather than
		// dropping the event.
		return true
	}
	counted.count.Add(1)
	return !counted.logged.Swap(true)
}

// LogSummaryEvery writes one summary per interval until ctx is done, and a final
// one on the way out so the last window is not lost to a restart.
func (t *Traffic) LogSummaryEvery(ctx context.Context, log *slog.Logger, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()

	windowStart := time.Now()
	for {
		select {
		case <-ctx.Done():
			t.logSummary(ctx, log, time.Since(windowStart))
			return
		case now := <-ticker.C:
			t.logSummary(ctx, log, now.Sub(windowStart))
			windowStart = now
		}
	}
}

// logSummary writes the window's counts and clears them. Counts are deltas, not
// totals: "zero spans accepted in the last minute" is the signal, and an
// ever-climbing total buries it.
func (t *Traffic) logSummary(ctx context.Context, log *slog.Logger, window time.Duration) {
	attrs := []any{
		slog.Int64(fieldAcceptedRequests, t.acceptedRequests.Swap(0)),
		slog.Int64(fieldAcceptedSpans, t.acceptedSpans.Swap(0)),
		slog.Int64(fieldRefusedRequests, t.refusedRequests.Swap(0)),
		slog.Int64(fieldRefusedSpans, t.refusedSpans.Swap(0)),
		slog.Int64(fieldDurationMillis, window.Milliseconds()),
	}
	for _, reason := range refusalReasons {
		attrs = append(attrs, slog.Int64(refusedPrefix+reason, t.take(reason)))
	}
	for _, reason := range degradationReasons {
		attrs = append(attrs, slog.Int64(reason, t.take(reason)))
	}
	log.InfoContext(ctx, summaryMessage, attrs...)
}

// take reads a reason's count and reopens it for one more log line next window.
func (t *Traffic) take(reason string) int64 {
	counted := t.tallies[reason]
	counted.logged.Store(false)
	return counted.count.Swap(0)
}

// reasonFor names a verdict for the tally. Only the codes the gates produce are
// listed; anything else is our bug, and internal is what a bug means.
func reasonFor(rpcCode code.Code) string {
	switch rpcCode {
	case code.Code_UNAUTHENTICATED:
		return reasonUnauthenticated
	case code.Code_RESOURCE_EXHAUSTED:
		return reasonThrottled
	case code.Code_INVALID_ARGUMENT:
		return reasonInvalid
	case code.Code_UNAVAILABLE:
		return reasonUnavailable
	default:
		return reasonInternal
	}
}
