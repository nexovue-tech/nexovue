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

	// reasonForbiddenOrigin is a key used from a page it was not issued for, or
	// a secret key used from a page at all. Counted separately from
	// unauthenticated because the key was good and the site was not, which is a
	// different customer conversation.
	reasonForbiddenOrigin = "forbidden_origin"

	reasonThrottled = "throttled"

	// reasonAllowanceSpent is a plan's monthly included volume used up. Both
	// this and reasonThrottled answer RESOURCE_EXHAUSTED, and they are counted
	// apart because they are different conversations: one clears in a second,
	// the other on the 1st, and only one of them is a support ticket.
	reasonAllowanceSpent = "allowance_spent"

	// reasonBrowserAllowanceSpent is the browser door's share of a split
	// allowance used up. Its own tally because it is its own
	// conversation: the server refusal means the free tier is over, this one
	// means the customer's site — or whoever copied its key — spent the
	// site's share, and the backend is still reporting.
	reasonBrowserAllowanceSpent = "browser_allowance_spent"

	// reasonOrganizationGone is ingest arriving for a deleted organization. Its
	// own tally because it is neither a plan nor a rate problem and never
	// clears: a key still authenticating for a deleted organization is a
	// deletion that did not finish, and a rising count here is the signal.
	reasonOrganizationGone = "organization_gone"

	// reasonIngestSuspended is the operator's stop valve closed on an
	// organization (P1-52). Its own tally because it is neither a plan nor a
	// rate problem and it is not the customer's doing: they are inside their
	// allowance and we stopped them anyway. A window of these is us, and it is
	// the only refusal here that a person has to lift by hand.
	reasonIngestSuspended = "ingest_suspended"

	// reasonThrottledInProcess is the fallback bound refusing while the shared
	// rate counter is unreachable. Counted apart from
	// reasonThrottled because the number it refused at is not the configured
	// one — it is a multiple of it, per receiver instance — and a support
	// conversation that cannot tell the two apart is unanswerable.
	reasonThrottledInProcess = "throttled_in_process"

	reasonInvalid     = "invalid"
	reasonUnavailable = "unavailable"
	reasonInternal    = "internal"

	// reasonLimiterDown is not a refusal: the limiter failing open still admits
	// the request. It is counted because losing the protection silently is how
	// the next incident starts.
	reasonLimiterDown = "limiter_unavailable"

	// reasonAllowanceUnknown is the same shape: the plan gate failing open
	// still admits the request, and a window of these is ingest nobody billed.
	reasonAllowanceUnknown = "allowance_unavailable"
)

// refusalReasons and degradationReasons fix the summary's shape: every window
// reports every counter, so a zero is a fact rather than a missing field.
var (
	// Every reason a gate can produce is listed. A reason missing from here is
	// not a cosmetic gap: it never appears in the summary, and `note` answers
	// "worth a log line" for every occurrence, so the one thing that was meant
	// to be counted quietly floods the log instead. `organization_gone` shipped
	// missing and did exactly that.
	refusalReasons = []string{
		reasonUnauthenticated, reasonForbiddenOrigin, reasonThrottled,
		reasonThrottledInProcess,
		reasonAllowanceSpent, reasonBrowserAllowanceSpent,
		reasonOrganizationGone, reasonIngestSuspended,
		reasonInvalid, reasonUnavailable, reasonInternal,
	}
	degradationReasons = []string{reasonLimiterDown, reasonAllowanceUnknown}
)

// Unit is what one signal's telemetry is counted in — the thing a customer
// would actually lose, and the unit OTLP's own partial_success counts
// rejections in. It is also the summary field each count appears under, so one
// door's throughput is queryable by name.
type Unit string

// The three units, named as OTLP names them so a summary field and a
// partial_success rejection count read as the same quantity.
const (
	Spans      Unit = "spans"
	LogRecords Unit = "log_records"
	DataPoints Unit = "data_points"
)

// units fixes the summary's shape on the signal axis, the way refusalReasons
// does on the reason axis: every window reports every door, so a door that took
// nothing shows a zero rather than a missing field. A door absent from this
// table would count nothing, which is the failure the summary exists to catch.
var units = []Unit{Spans, LogRecords, DataPoints}

// Summary line fields. Names follow the control plane's request log so all three
// services answer one query.
const (
	fieldAcceptedRequests = "accepted_requests"
	fieldRefusedRequests  = "refused_requests"
	fieldDurationMillis   = "duration_ms"
	fieldMethod           = "method"
	fieldPath             = "path"
	fieldStatus           = "status"
	fieldReason           = "reason"
	fieldError            = "error"

	// Who sent it. A refusal is turned away before it has a tenant, so without
	// these two a window of them names no sender at all.
	fieldPeerAddress = "peer_address"
	fieldUserAgent   = "user_agent"

	// fieldOrigin is the site a browser named. It is what turns a window of
	// forbidden_origin refusals from a number into an address to check.
	fieldOrigin = "origin"

	// fieldItems is the telemetry count on one event line. The line's path says
	// which door it came from, so the field itself stays signal-neutral and one
	// query reads every door.
	fieldItems = "items"

	// These turn a unit or a reason into its summary field, so the two names
	// cannot drift apart.
	acceptedPrefix = "accepted_"
	refusedPrefix  = "refused_"
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
	// Requests are counted across every door: a request is a request whatever
	// signal it carried. Telemetry is counted per door, because a logs door
	// that has gone silent is invisible in a total the traces door dominates.
	acceptedRequests atomic.Int64
	refusedRequests  atomic.Int64

	// Keys of both maps are fixed at construction, so concurrent writers only
	// touch the counters, never the maps.
	items   map[Unit]*itemCounts
	tallies map[string]*tally
}

// itemCounts is one signal's telemetry for the window.
type itemCounts struct {
	accepted atomic.Int64
	refused  atomic.Int64
}

// tally is one reason's count for the window, plus whether it has already been
// logged in this one.
type tally struct {
	count  atomic.Int64
	logged atomic.Bool
}

// NewTraffic returns an empty tally with every door and every reason already
// present.
func NewTraffic() *Traffic {
	traffic := &Traffic{
		items:   make(map[Unit]*itemCounts),
		tallies: make(map[string]*tally),
	}
	for _, unit := range units {
		traffic.items[unit] = &itemCounts{}
	}
	for _, reason := range refusalReasons {
		traffic.tallies[reason] = &tally{}
	}
	for _, reason := range degradationReasons {
		traffic.tallies[reason] = &tally{}
	}
	return traffic
}

// Accepted records an export the door published, in the unit that door counts.
func (t *Traffic) Accepted(unit Unit, items int) {
	t.acceptedRequests.Add(1)
	if counted, known := t.items[unit]; known {
		counted.accepted.Add(int64(items))
	}
}

// Refused records an export the door turned away, reporting whether this is the
// first of its reason in the current window and therefore worth a log line.
func (t *Traffic) Refused(unit Unit, reason string, items int) bool {
	t.refusedRequests.Add(1)
	if counted, known := t.items[unit]; known {
		counted.refused.Add(int64(items))
	}
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
		slog.Int64(fieldRefusedRequests, t.refusedRequests.Swap(0)),
		slog.Int64(fieldDurationMillis, window.Milliseconds()),
	}
	for _, unit := range units {
		counted := t.items[unit]
		attrs = append(attrs,
			slog.Int64(acceptedPrefix+string(unit), counted.accepted.Swap(0)),
			slog.Int64(refusedPrefix+string(unit), counted.refused.Swap(0)))
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

// reasonFor names a verdict for the tally from its rpc code alone. It is the
// default; a gate whose code is shared with another says which it is with
// `countedUnder`. Only the codes the gates produce are listed; anything else is
// our bug, and internal is what a bug means.
func reasonFor(rpcCode code.Code) string {
	switch rpcCode {
	case code.Code_UNAUTHENTICATED:
		return reasonUnauthenticated
	case code.Code_PERMISSION_DENIED:
		return reasonForbiddenOrigin
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
