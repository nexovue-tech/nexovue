package logging_test

import (
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
)

func TestParseTraceparent(t *testing.T) {
	const (
		traceID = "4bf92f3577b34da6a3ce929d0e0e4736"
		spanID  = "00f067aa0ba902b7"
	)

	cases := map[string]struct {
		header string
		want   logging.Fields
	}{
		"sampled": {
			header: "00-" + traceID + "-" + spanID + "-01",
			want:   logging.Fields{TraceID: traceID, SpanID: spanID},
		},
		"not sampled is still a trace": {
			header: "00-" + traceID + "-" + spanID + "-00",
			want:   logging.Fields{TraceID: traceID, SpanID: spanID},
		},
		"absent":            {header: "", want: logging.Fields{}},
		"wrong field count": {header: "00-" + traceID + "-" + spanID, want: logging.Fields{}},
		"all-zero trace id": {
			header: "00-" + "00000000000000000000000000000000" + "-" + spanID + "-01",
			want:   logging.Fields{},
		},
		"forbidden version": {
			header: "ff-" + traceID + "-" + spanID + "-01",
			want:   logging.Fields{},
		},
		"uppercase hex": {
			header: "00-4BF92F3577B34DA6A3CE929D0E0E4736-" + spanID + "-01",
			want:   logging.Fields{},
		},
		"short span id": {header: "00-" + traceID + "-00f067aa-01", want: logging.Fields{}},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := logging.ParseTraceparent(tc.header); got != tc.want {
				t.Errorf("ParseTraceparent(%q) = %+v, want %+v", tc.header, got, tc.want)
			}
		})
	}
}

func TestWithFieldsMergesRatherThanReplaces(t *testing.T) {
	ctx := logging.WithFields(t.Context(), logging.Fields{TraceID: "abc"})
	ctx = logging.WithFields(ctx, logging.Fields{OrgID: "org-1"})

	got := logging.FieldsFrom(ctx)
	if got.TraceID != "abc" {
		t.Errorf("TraceID = %q, want the earlier value to survive", got.TraceID)
	}
	if got.OrgID != "org-1" {
		t.Errorf("OrgID = %q, want the later value to be added", got.OrgID)
	}
}
