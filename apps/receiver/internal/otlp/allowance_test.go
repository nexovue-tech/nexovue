package otlp_test

import (
	"bytes"
	"log/slog"
	"net/http"
	"strings"
	"testing"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/grpc/codes"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/allowance"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// The free tier as migration 0015 seeds it: 2 GiB per billed service, three
// billed services, so 6 GiB pooled across the organization for the month.
const (
	gib               = int64(1) << 30
	freeMonthIncluded = 3 * 2 * gib
)

// spentFreeMonth is an organization that has ingested one byte more than its
// free month includes.
var spentFreeMonth = staticUsage{usage: allowance.Usage{
	IncludedBytes: freeMonthIncluded,
	Enforced:      true,
	IngestedBytes: freeMonthIncluded + 1,
	Found:         true,
}}

// payingOrganization is what pro and scale look like: no ceiling on billed
// services, so no allowance is enforced however much arrives.
var payingOrganization = staticUsage{usage: allowance.Usage{
	Enforced:      false,
	IngestedBytes: 900 * gib,
	Found:         true,
}}

func TestAnExportInsideTheAllowanceIsPublished(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: staticUsage{usage: allowance.Usage{
		IncludedBytes: freeMonthIncluded,
		Enforced:      true,
		IngestedBytes: freeMonthIncluded - 1,
		Found:         true,
	}}})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d: the last included byte is included",
			response.Code, http.StatusOK)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want 1", len(d.producer.records))
	}
}

// The whole point: a free month that is over stops accepting, and does it
// before the body is read.
func TestASpentAllowanceIsRefusedBeforeTheBodyIsRead(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	// A body far larger than the cap: if it were read, the answer would be 413.
	response := post(t, d, bytes.Repeat([]byte{0}, 4<<20), nil)

	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d: the plan gate must answer before decoding",
			response.Code, http.StatusTooManyRequests)
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_RESOURCE_EXHAUSTED) {
		t.Errorf("status code = %d, want RESOURCE_EXHAUSTED", got)
	}
	if len(d.producer.records) != 0 {
		t.Error("a refused export reached the broker")
	}
}

// The spec makes RESOURCE_EXHAUSTED non-retryable unless the server attaches
// RetryInfo. A plan refusal lasts until the 1st, so attaching one would have a
// compliant client resend the same batch for three weeks — and the Collector
// drops a queued batch after five minutes anyway, so the data is lost either
// way. The rate limiter's refusal does carry one, because that one recovers.
func TestASpentAllowanceIsNotRetryable(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if got := response.Header().Get("Retry-After"); got != "" {
		t.Errorf("Retry-After = %q, want none: a client must not resend until the 1st", got)
	}
}

func TestASpentAllowanceOverGRPCCarriesNoRetryInfo(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	_, err := d.export(t, exportRequest(traceID(0x11)))

	decoded := grpcStatus(t, err)
	if decoded.Code() != codes.ResourceExhausted {
		t.Fatalf("code = %v, want ResourceExhausted", decoded.Code())
	}
	if delay, present := retryDelay(t, decoded); present {
		t.Errorf("RetryInfo = %v, want none: this refusal lasts until the 1st", delay)
	}
}

// A refusal nobody can read is the defect this is meant to prevent. The message
// says what ran out and when it comes back.
func TestTheRefusalSaysWhatRanOutAndWhenItReturns(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	message := rpcStatus(t, response).GetMessage()
	if !strings.Contains(message, "monthly ingest volume") {
		t.Errorf("message = %q, want it to name the volume allowance", message)
	}
	if !strings.Contains(message, "resumes on") {
		t.Errorf("message = %q, want it to name the day ingestion resumes", message)
	}
}

// Refusing a customer who pays would be a far larger failure than the hole this
// gate closes. Pro and scale record no `max_billed_services`, and null is
// uncapped.
func TestAPayingOrganizationIsNeverRefusedForVolume(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: payingOrganization})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d: an uncapped plan has no allowance to spend",
			response.Code, http.StatusOK)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want 1", len(d.producer.records))
	}
}

func TestAPayingOrganizationIsNeverRefusedOverGRPC(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{allowanceUsage: payingOrganization})

	if _, err := d.export(t, exportRequest(traceID(0x11))); err != nil {
		t.Fatalf("export = %v, want no error: an uncapped plan has no allowance to spend", err)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want 1", len(d.producer.records))
	}
}

// A control-plane blip must not become an ingest outage: the same call the rate
// limiter makes, for the same reason.
func TestAnUnreadableAllowanceFailsOpen(t *testing.T) {
	written := &bytes.Buffer{}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{
		allowanceUsage: staticUsage{err: errUnreachableControlPlane},
		log:            slog.New(slog.NewJSONHandler(written, nil)),
		traffic:        traffic,
	})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d: telemetry must survive a control-plane blip",
			response.Code, http.StatusOK)
	}
	summary := summaryLine(t, runSummary(t, traffic))
	if summary["allowance_unavailable"] != float64(1) {
		t.Errorf("allowance_unavailable = %v, want 1: losing the gate silently is how "+
			"the next unbilled month starts", summary["allowance_unavailable"])
	}
}

// Both this and the rate limiter answer RESOURCE_EXHAUSTED, and a window that
// folded them together would report a customer whose free month ended as one
// sending too fast.
func TestAPlanRefusalIsCountedApartFromARateLimit(t *testing.T) {
	written := &bytes.Buffer{}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{
		allowanceUsage: spentFreeMonth,
		log:            slog.New(slog.NewJSONHandler(written, nil)),
		traffic:        traffic,
	})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	var refusal map[string]any
	for _, line := range recordedLines(t, written) {
		if line["msg"] == "export refused" {
			refusal = line
		}
	}
	if refusal == nil {
		t.Fatalf("no refusal was logged (%s)", written)
	}
	if refusal["reason"] != "allowance_spent" {
		t.Errorf("reason = %v, want allowance_spent", refusal["reason"])
	}

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["refused_allowance_spent"] != float64(1) {
		t.Errorf("refused_allowance_spent = %v, want 1", summary["refused_allowance_spent"])
	}
	if summary["refused_throttled"] != float64(0) {
		t.Errorf("refused_throttled = %v, want 0: a spent month is not a rate limit",
			summary["refused_throttled"])
	}
}

// The plan boundary belongs to the organization, so it cannot be sidestepped by
// sending from a page instead of a server.
func TestTheBrowserDoorIsHeldToTheSameAllowance(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	response := postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin, "X-Nexovue-Key": validSiteKey})

	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d: a spent month is spent at every door",
			response.Code, http.StatusTooManyRequests)
	}
}

// Every signal is refused, not just the one most tests exercise: the allowance
// is bytes across all three.
func TestAllThreeSignalsAreHeldToTheAllowance(t *testing.T) {
	for _, path := range []string{otlp.TracesPath, otlp.LogsPath, otlp.MetricsPath} {
		t.Run(path, func(t *testing.T) {
			d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

			response := postTo(t, d, path, nil, nil)

			if response.Code != http.StatusTooManyRequests {
				t.Errorf("status = %d, want %d", response.Code, http.StatusTooManyRequests)
			}
		})
	}
}
