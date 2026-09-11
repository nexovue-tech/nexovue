package logwriting

import (
	"testing"

	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
)

// The numeric column is the one the sort key orders by, so a level the mapping
// does not recognise costs a filter, not just a label.
func TestSeverityTextMapsOntoTheOTLPScale(t *testing.T) {
	cases := map[string]int32{
		"TRACE": 1, "trace": 1, "  Trace  ": 1,
		"DEBUG": 5, "dbg": 5, "D": 5,
		"INFO": 9, "information": 9, "notice": 10,
		"WARN": 13, "WARNING": 13, "warn": 13, "W": 13,
		"ERROR": 17, "err": 17, "SEVERE": 17,
		"FATAL": 21, "critical": 21, "panic": 21,
		"INFO2": 10, "warn3": 15, "ERROR4": 20,
		"": 0, "chatty": 0, "notice2": 0,
	}
	for text, want := range cases {
		if got := severityNumberFromText(text); got != want {
			t.Errorf("severityNumberFromText(%q) = %d, want %d", text, got, want)
		}
	}
}

// A record that carries OTLP's own number is already on the scale; mapping its
// text again could only disagree with it.
func TestDeclaredSeverityNumberWins(t *testing.T) {
	record := &logspb.LogRecord{
		SeverityText:   "oops",
		SeverityNumber: logspb.SeverityNumber_SEVERITY_NUMBER_ERROR,
	}
	text, number := severityOf(record)
	if text != "oops" || number != 17 {
		t.Errorf("severityOf = (%q, %d), want (\"oops\", 17)", text, number)
	}
}

func TestSeverityFallsBackToTheText(t *testing.T) {
	record := &logspb.LogRecord{SeverityText: "WARNING"}
	text, number := severityOf(record)
	if text != "WARNING" || number != 13 {
		t.Errorf("severityOf = (%q, %d), want (\"WARNING\", 13)", text, number)
	}
}

func TestSeverityIsZeroWhenTheRecordCarriesNone(t *testing.T) {
	text, number := severityOf(&logspb.LogRecord{})
	if text != "" || number != severityUnspecified {
		t.Errorf("severityOf = (%q, %d), want (\"\", 0)", text, number)
	}
}
