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

// On a row recording no split, the one allowance belongs to the organization
// and cannot be sidestepped by sending from a page instead of a server. That is
// every row seeded before the split, and it stays true; where a row does record
// a split, the two doors are judged apart — see below.
func TestOnAPooledRowTheBrowserDoorIsHeldToTheSameAllowance(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: spentFreeMonth})

	response := postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))),
		map[string]string{"Origin": pageOrigin, "X-Nexovue-Key": validSiteKey})

	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d: a spent month is spent at every door",
			response.Code, http.StatusTooManyRequests)
	}
}

/* ------------------------------------ the split between the two doors */

// The free month as migration 0016 seeds it: a quarter of the 6 GiB is the
// browser door's, the rest the server's. The figures below are the row's own
// columns, not a verdict, so the arithmetic that decides a refusal runs in the
// gate under test rather than in this file.
const (
	freeBrowserShare = 25
	freeBrowserBytes = freeMonthIncluded / 4
	freeServerBytes  = freeMonthIncluded - freeBrowserBytes
)

func splitFreeMonth(ingested, browserIngested int64) staticUsage {
	return staticUsage{usage: allowance.Usage{
		IncludedBytes:        freeMonthIncluded,
		Enforced:             true,
		BrowserSharePercent:  freeBrowserShare,
		Split:                true,
		IngestedBytes:        ingested,
		BrowserIngestedBytes: browserIngested,
		Found:                true,
	}}
}

// browserShareSpent is the attack the split closes: a public key, scraped from
// the page it is printed in, has sent many months' worth through the browser
// door, and the customer's servers have sent nothing.
var browserShareSpent = splitFreeMonth(50*freeMonthIncluded, 50*freeMonthIncluded)

// serverOnlyAtTheOldPartitionLine is a backend that has spent what the
// partition used to give it — 75% of the plan — with the site's share
// untouched. Under the partition this was refused. Under the cap it is not:
// nothing has spent the browser's share, so it is still the server's to use.
var serverOnlyAtTheOldPartitionLine = splitFreeMonth(freeServerBytes+1, 0)

// wholePlanSpent is a backend one byte past the plan's whole allowance, with
// the site's share untouched. This is what closes the server door now.
var wholePlanSpent = splitFreeMonth(freeMonthIncluded+1, 0)

var siteKeyFromPage = map[string]string{"Origin": pageOrigin, "X-Nexovue-Key": validSiteKey}

// The property protected: a customer's backend keeps reporting no matter what
// happens to their website. Asserted at the door, both transports.
func TestASpentBrowserShareRefusesThePageAndAdmitsTheServer(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: browserShareSpent})

	page := postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))), siteKeyFromPage)
	if page.Code != http.StatusTooManyRequests {
		t.Fatalf("browser door status = %d, want %d: the site's share is spent",
			page.Code, http.StatusTooManyRequests)
	}

	server := post(t, d, encodedRequest(t, exportRequest(traceID(0x22))), nil)
	if server.Code != http.StatusOK {
		t.Fatalf("server door status = %d, want %d: the backend must keep reporting "+
			"whatever happens to the website (%s)", server.Code, http.StatusOK, server.Body)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want the server's 1", len(d.producer.records))
	}
}

func TestASpentBrowserShareAdmitsTheServerOverGRPC(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{allowanceUsage: browserShareSpent})

	if _, err := d.export(t, exportRequest(traceID(0x11))); err != nil {
		t.Fatalf("export = %v, want no error: the browser's share is not the server's", err)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("%d records reached the broker, want 1", len(d.producer.records))
	}
}

// The shares are a partition of the plan's total, so the server's own share
// ending does not open the browser's to it, and does not close the browser's.
// The share is a cap, not a reservation, and this is that at the door. A
// backend at 75% of the plan with no browser telemetry at all keeps sending:
// the browser's share is only withheld once a browser actually spends it.
//
// This replaces a test asserting the opposite — that the same backend was
// refused while the untouched browser share sat beside it. That was the
// partition, and it withdrew a quarter of the free tier for a door nobody could
// reach, since the browser SDK is P1-39 and unbuilt.
func TestABackendIsNotRefusedForABrowserShareNobodyHasSpent(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: serverOnlyAtTheOldPartitionLine})

	server := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)
	if server.Code != http.StatusOK {
		t.Fatalf("server door status = %d, want %d: the browser share is unspent and so still the server's (%s)",
			server.Code, http.StatusOK, server.Body)
	}
}

// The other side of the same rule: once the plan's whole allowance is gone,
// every door closes. A cap on one door must not become a way to send more than
// the month includes.
func TestThePlansWholeAllowanceSpentClosesBothDoors(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: wholePlanSpent})

	server := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)
	if server.Code != http.StatusTooManyRequests {
		t.Errorf("server door status = %d, want %d: the plan's allowance is spent",
			server.Code, http.StatusTooManyRequests)
	}

	page := postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x22))), siteKeyFromPage)
	if page.Code != http.StatusTooManyRequests {
		t.Errorf("browser door status = %d, want %d: the browser door may not spend a plan the server filled (%s)",
			page.Code, http.StatusTooManyRequests, page.Body)
	}
}

// A customer refused from their page must not be told their month is over —
// it is not — and one refused from their servers must not be told to look at
// their site.
func TestASplitRefusalNamesTheShareThatRanOut(t *testing.T) {
	pageRefusal := postTo(t, newDoor(t, doorOptions{allowanceUsage: browserShareSpent}),
		otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))), siteKeyFromPage)
	if message := rpcStatus(t, pageRefusal).GetMessage(); !strings.Contains(message, "browser ingest volume") {
		t.Errorf("browser refusal = %q, want it to name the browser's share", message)
	}

	serverRefusal := post(t, newDoor(t, doorOptions{allowanceUsage: wholePlanSpent}),
		encodedRequest(t, exportRequest(traceID(0x11))), nil)
	message := rpcStatus(t, serverRefusal).GetMessage()
	if !strings.Contains(message, "ingest volume") || strings.Contains(message, "browser ingest volume") {
		t.Errorf("server refusal = %q, want it to name the plan's own volume", message)
	}
	if !strings.Contains(message, "resumes on") {
		t.Errorf("server refusal = %q, want it to name the day ingestion resumes", message)
	}
}

// A window of browser refusals is the site — or whoever copied its key —
// spending the site's share while the backend still reports. A window of server
// refusals is the free tier ending. Folding them would hide the very case the
// split exists for.
func TestABrowserShareRefusalIsCountedApartFromTheServers(t *testing.T) {
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{allowanceUsage: browserShareSpent, traffic: traffic})

	postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))), siteKeyFromPage)

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["refused_browser_allowance_spent"] != float64(1) {
		t.Errorf("refused_browser_allowance_spent = %v, want 1",
			summary["refused_browser_allowance_spent"])
	}
	if summary["refused_allowance_spent"] != float64(0) {
		t.Errorf("refused_allowance_spent = %v, want 0: the site's share is not the month",
			summary["refused_allowance_spent"])
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
