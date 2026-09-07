package otlp_test

import (
	"bytes"
	"net/http"
	"strings"
	"testing"

	"google.golang.org/genproto/googleapis/rpc/code"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/allowance"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// suspendedOrganization is the case the valve exists for: a tenant well inside
// its free month, stopped because of what it is doing to the platform. The
// allowance has nothing to refuse it with.
var suspendedOrganization = staticUsage{usage: allowance.Usage{
	IncludedBytes:   freeMonthIncluded,
	Enforced:        true,
	IngestedBytes:   1,
	Found:           true,
	IngestSuspended: true,
}}

// The valve answers where the allowance answers, which is before the body is
// read. A kill switch that decoded a runaway tenant's payload first would spend
// on the runaway exactly what it was closed to stop spending.
func TestASuspendedOrganizationIsRefusedBeforeTheBodyIsRead(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: suspendedOrganization})

	// Far larger than the body cap: if it were read, the answer would be 413.
	response := post(t, d, bytes.Repeat([]byte{0}, 4<<20), nil)

	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d: the valve must answer before decoding",
			response.Code, http.StatusTooManyRequests)
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_RESOURCE_EXHAUSTED) {
		t.Errorf("status code = %d, want RESOURCE_EXHAUSTED", got)
	}
	if len(d.producer.records) != 0 {
		t.Error("a suspended organization's export reached the broker")
	}
}

// Two different conversations, so two different messages. A suspended customer
// told their month ran out would wait for the 1st, and the 1st would not fix
// it: only a person reopening the valve does.
func TestTheSuspensionRefusalDoesNotReadLikeASpentAllowance(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: suspendedOrganization})

	response := post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	message := rpcStatus(t, response).GetMessage()
	if !strings.Contains(message, "suspended") {
		t.Errorf("message = %q, want it to say ingestion is suspended", message)
	}
	if strings.Contains(message, "resumes on") {
		t.Errorf("message = %q, want no reset date: a suspension has none to name", message)
	}
	if strings.Contains(message, "monthly ingest volume") {
		t.Errorf("message = %q, want it not to blame the plan: this customer is inside it",
			message)
	}
}

// A window of these is us, not the customer, so it cannot be folded into the
// tally that means a free month ended.
func TestASuspensionIsCountedApartFromASpentAllowance(t *testing.T) {
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{allowanceUsage: suspendedOrganization, traffic: traffic})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	summary := summaryLine(t, runSummary(t, traffic))
	if summary["refused_ingest_suspended"] != float64(1) {
		t.Errorf("refused_ingest_suspended = %v, want 1", summary["refused_ingest_suspended"])
	}
	if summary["refused_allowance_spent"] != float64(0) {
		t.Errorf("refused_allowance_spent = %v, want 0: a suspension is not a spent month",
			summary["refused_allowance_spent"])
	}
}

// Both doors, because a tenant stopped for hurting the platform is stopped
// everywhere, and the browser door is the one a scraped key would keep using.
func TestASuspensionClosesTheBrowserDoorToo(t *testing.T) {
	d := newDoor(t, doorOptions{allowanceUsage: suspendedOrganization})

	response := postTo(t, d, otlp.BrowserPathPrefix+otlp.TracesPath,
		encodedRequest(t, exportRequest(traceID(0x11))), siteKeyFromPage)

	if response.Code != http.StatusTooManyRequests {
		t.Errorf("status = %d, want %d: the valve must close the browser door as well",
			response.Code, http.StatusTooManyRequests)
	}
}

// The tally for a deleted organization shipped missing from the summary's
// reason table. It cost two things at once: the count never appeared in any
// summary, and `note` answered "worth a log line" for every single refusal, so
// the one event meant to be reported quietly wrote a WARN per request instead.
func TestEveryGateReasonReachesTheSummary(t *testing.T) {
	deleted := staticUsage{usage: allowance.Usage{Found: true, Deleted: true}}
	traffic := otlp.NewTraffic()
	d := newDoor(t, doorOptions{allowanceUsage: deleted, traffic: traffic})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	summary := summaryLine(t, runSummary(t, traffic))
	if _, reported := summary["refused_organization_gone"]; !reported {
		t.Fatal("refused_organization_gone is absent from the summary: a reason missing " +
			"from the table is never counted and is logged on every occurrence")
	}
	if summary["refused_organization_gone"] != float64(1) {
		t.Errorf("refused_organization_gone = %v, want 1",
			summary["refused_organization_gone"])
	}
}
