package otlp_test

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// fallbackBurstMultiple restated. ratelimit keeps it unexported, so this is the
// number a door test has to know to send past the bound; if the two drift, the
// burst below stops being a burst and this file stops testing anything, which
// is why every assertion here is written against a count rather than a guess.
const fallbackBurstMultiple = 10

// exportsUntilTheFallbackRefuses sends until the door turns one away, and
// reports the response that refused and how many got through first. It gives up
// well past the bound rather than looping forever, so a fallback that never
// engages fails as a wrong number instead of a hung suite.
func exportsUntilTheFallbackRefuses(
	t *testing.T,
	d door,
	attempts int,
) (*httptest.ResponseRecorder, int) {
	t.Helper()
	body := encodedRequest(t, exportRequest(traceID(0x11)))
	for sent := range attempts {
		response := post(t, d, body, nil)
		if response.Code != http.StatusOK {
			return response, sent
		}
	}
	return nil, attempts
}

// The fallback's first property, and the one that must not regress: the door
// stays open when the shared counter cannot answer. The fallback bounds what
// "open" costs; it does not close the door.
func TestTheFallbackStillAdmitsTheFirstExportsOfAnOutage(t *testing.T) {
	const configured = 3
	d := newDoor(t, doorOptions{counter: brokenCounter{}, requestsPerSec: configured})

	_, admitted := exportsUntilTheFallbackRefuses(t, d, configured*fallbackBurstMultiple*4)

	if admitted < configured {
		t.Errorf("only %d exports were admitted before the fallback refused, want at least "+
			"the configured %d: a Valkey blip must not become an ingest outage",
			admitted, configured)
	}
}

// And the second: open is not unbounded. Without this bound no per-second limit
// applies to anyone for the length of the outage, and an outage is when the
// platform has least headroom to absorb a burst.
func TestTheFallbackBoundsABurstWhileTheCounterIsDown(t *testing.T) {
	const configured = 3
	burst := configured * fallbackBurstMultiple
	d := newDoor(t, doorOptions{counter: brokenCounter{}, requestsPerSec: configured})

	refusal, admitted := exportsUntilTheFallbackRefuses(t, d, burst*4)

	if refusal == nil {
		t.Fatalf("all %d exports were admitted; the in-process bound never engaged", burst*4)
	}
	if admitted > burst {
		t.Errorf("admitted %d before refusing, want at most the in-process bound of %d",
			admitted, burst)
	}
	if refusal.Code != http.StatusTooManyRequests {
		t.Errorf("status = %d, want %d", refusal.Code, http.StatusTooManyRequests)
	}
	if refusal.Header().Get("Retry-After") == "" {
		t.Error("a fallback refusal must tell the client how long to wait; this one clears")
	}
}

// A refusal during an outage will not match the configured number. The log line
// has to name which limit refused, or the first support conversation about it
// is unanswerable.
func TestTheFallbackRefusalNamesWhichLimitRefused(t *testing.T) {
	const configured = 2
	written := &bytes.Buffer{}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{
		counter:        brokenCounter{},
		requestsPerSec: configured,
		traffic:        traffic,
		log:            slog.New(slog.NewJSONHandler(written, nil)),
	})

	refusal, _ := exportsUntilTheFallbackRefuses(t, d, configured*fallbackBurstMultiple*4)
	if refusal == nil {
		t.Fatal("the in-process bound never refused anything")
	}

	// The customer's half: not the configured limit, and the message says so.
	message := rpcStatus(t, refusal).GetMessage()
	if !strings.Contains(message, "shared rate counter is unavailable") {
		t.Errorf("message = %q, want it to say the shared counter is down: a customer told "+
			"only 'rate limit exceeded' would check their own limit and be under it", message)
	}

	// Ours: the number that actually refused, which is the multiple.
	line := refusalLine(t, written)
	perInstance := strconv.Itoa(configured * fallbackBurstMultiple)
	if got, _ := line["error"].(string); !strings.Contains(got, perInstance) {
		t.Errorf("error = %q, want it to name the bound of %s/s that refused", got, perInstance)
	}

	// And it is counted apart from an ordinary throttle, for the same reason.
	summary := summaryLine(t, runSummary(t, traffic))
	if summary["refused_throttled_in_process"] != float64(1) {
		t.Errorf("refused_throttled_in_process = %v, want 1",
			summary["refused_throttled_in_process"])
	}
	if summary["refused_throttled"] != float64(0) {
		t.Errorf("refused_throttled = %v, want 0: the shared limit refused nothing, it was down",
			summary["refused_throttled"])
	}
}

// The fallback is not a second degradation reason. `limiter_unavailable` is
// still the one line per window that says the protection is degraded.
func TestTheDegradedLineIsUnchangedByTheFallback(t *testing.T) {
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{counter: brokenCounter{}, traffic: traffic})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["limiter_unavailable"] != float64(1) {
		t.Errorf("limiter_unavailable = %v, want 1", summary["limiter_unavailable"])
	}
}
