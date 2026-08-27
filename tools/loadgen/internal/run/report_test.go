package run

import (
	"bytes"
	"regexp"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/measure"
)

// spansAcceptedLine is how tools/chaos reads the acknowledged count out of this
// report. Keeping a copy here means a reformat of that line fails in loadgen's
// own tests rather than in a chaos run that takes the stack down.
var spansAcceptedLine = regexp.MustCompile(`spans accepted\s+(\d+)`)

func TestReportDistinguishesTheThreeOutcomes(t *testing.T) {
	cases := []struct {
		name      string
		transport measure.Result
		want      []string
		absent    []string
	}{
		{
			name: "everything was answered",
			transport: measure.Result{
				Requests: 10, SpansOffered: 5120, SpansAccepted: 5120,
			},
			want:   []string{"spans accepted     5120", "spans unknown      0"},
			absent: []string{"never sent", "spans refused"},
		},
		{
			name: "some exports were refused",
			transport: measure.Result{
				Requests: 10, SpansOffered: 5120, SpansAccepted: 4608, SpansRefused: 512,
			},
			want:   []string{"spans refused      512", "spans unknown      0"},
			absent: []string{"never sent"},
		},
		{
			name: "an export went out and was never answered",
			transport: measure.Result{
				Requests: 10, SpansOffered: 5120, SpansAccepted: 4608,
				UnknownRequests: 1, UnknownSpans: 512,
			},
			// The tolerance has to be stated, not left for a reader to infer.
			want: []string{
				"spans unknown      512 in 1 requests",
				"downstream count above accepted is expected",
				"at least this",
			},
			absent: []string{"never sent"},
		},
		{
			name: "an export never left this process",
			transport: measure.Result{
				Requests: 10, SpansOffered: 4608, SpansAccepted: 4608,
				NotSentRequests: 1, NotSentSpans: 512,
			},
			want: []string{"never sent         512 spans in 1 requests", "nowhere downstream"},
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			printed := writeReport(t, Report{Transport: c.transport})

			for _, want := range c.want {
				if !strings.Contains(printed, want) {
					t.Errorf("report is missing %q:\n%s", want, printed)
				}
			}
			for _, absent := range c.absent {
				if strings.Contains(printed, absent) {
					t.Errorf("report should not mention %q when it did not happen:\n%s", absent, printed)
				}
			}
		})
	}
}

func TestAcceptedCountStaysReadableToTheChaosTest(t *testing.T) {
	printed := writeReport(t, Report{Transport: measure.Result{
		Requests: 10, SpansOffered: 5120, SpansAccepted: 4608,
		UnknownRequests: 1, UnknownSpans: 512,
	}})

	match := spansAcceptedLine.FindStringSubmatch(printed)
	if match == nil {
		t.Fatalf("no readable 'spans accepted' line:\n%s", printed)
	}
	if match[1] != "4608" {
		t.Errorf("accepted count read back as %q, want 4608", match[1])
	}
}

// The latency population is the answered requests, so the report must not
// suggest the percentiles cover exports that never got a reply.
func TestLatencyIsAttributedToAnsweredRequestsOnly(t *testing.T) {
	printed := writeReport(t, Report{Transport: measure.Result{
		Requests: 10, UnknownRequests: 2, NotSentRequests: 1,
	}})

	if !strings.Contains(printed, "over the 7 requests that were answered") {
		t.Errorf("report does not name the latency population:\n%s", printed)
	}
}

func writeReport(t *testing.T, report Report) string {
	t.Helper()
	var out bytes.Buffer
	if err := report.Write(&out); err != nil {
		t.Fatalf("writing the report: %v", err)
	}
	return out.String()
}
