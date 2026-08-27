package measure

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"syscall"
	"testing"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

func TestInspectCountsSpansAcrossResourcesAndScopes(t *testing.T) {
	first := []byte("0123456789abcdef")
	second := []byte("fedcba9876543210")

	body := marshalExport(t, &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{
			{ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{{TraceId: first}, {TraceId: first}}},
				{Spans: []*tracepb.Span{{TraceId: second}}},
			}},
			{ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{{TraceId: second}}},
			}},
		},
	})

	got := inspect(body)
	if got.spans != 4 {
		t.Errorf("spans = %d, want 4", got.spans)
	}
	// Lower-case hex is how the receiver keys Kafka and how ClickHouse stores
	// the column the lag query matches on.
	if want := "30313233343536373839616263646566"; got.traceID != want {
		t.Errorf("traceID = %q, want %q", got.traceID, want)
	}
}

// A body we cannot read is still forwarded, because the receiver's answer to it
// is a result worth recording. It simply contributes no spans.
func TestInspectTreatsAnUndecodableBodyAsNoSpans(t *testing.T) {
	got := inspect([]byte("not a protobuf export"))
	if got.spans != 0 || got.traceID != "" {
		t.Errorf("inspect(garbage) = %+v, want an empty export", got)
	}
}

func TestSampleClockSpacesSamplesByInterval(t *testing.T) {
	clock := newSampleClock()
	base := time.Unix(0, 0)
	interval := time.Second

	if !clock.due(base, interval) {
		t.Fatal("the first sample should always be taken")
	}
	if clock.due(base.Add(999*time.Millisecond), interval) {
		t.Error("a sample inside the interval should be skipped")
	}
	if !clock.due(base.Add(time.Second), interval) {
		t.Error("a sample at the interval should be taken")
	}
}

func TestHopByHopHeadersAreNotForwarded(t *testing.T) {
	source := map[string][]string{
		"X-Nexovue-Key":     {"nvk_live_test"},
		"Content-Type":      {"application/x-protobuf"},
		"Connection":        {"keep-alive"},
		"Transfer-Encoding": {"chunked"},
	}
	destination := map[string][]string{}
	copyHeaders(destination, source)

	if _, forwarded := destination["Connection"]; forwarded {
		t.Error("Connection is hop-by-hop and must not be forwarded")
	}
	// The ingest key must survive: the auth gate is inside the measured path.
	if got := destination["X-Nexovue-Key"]; len(got) != 1 || got[0] != "nvk_live_test" {
		t.Errorf("X-Nexovue-Key = %v, want it forwarded unchanged", got)
	}
}

// Only a failure to dial proves the receiver never saw the request. Everything
// later has to be treated as possibly published, so the default is "unknown".
func TestNeverReachedReceiverOnlyClaimsDialFailures(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		{
			name: "dial refused, so no socket ever existed",
			err:  &url.Error{Op: "Post", Err: &net.OpError{Op: "dial", Err: syscall.ECONNREFUSED}},
			want: true,
		},
		{
			name: "connection reset while reading the answer",
			err:  &url.Error{Op: "Post", Err: &net.OpError{Op: "read", Err: syscall.ECONNRESET}},
			want: false,
		},
		{
			name: "write failed part way through the request",
			err:  &url.Error{Op: "Post", Err: &net.OpError{Op: "write", Err: syscall.EPIPE}},
			want: false,
		},
		{
			name: "request timed out waiting for an answer",
			err:  &url.Error{Op: "Post", Err: context.DeadlineExceeded},
			want: false,
		},
		{
			name: "the inbound client gave up",
			err:  &url.Error{Op: "Post", Err: context.Canceled},
			want: false,
		},
		{
			name: "a failure we do not recognise",
			err:  errors.New("transport gave up"),
			want: false,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := neverReachedReceiver(c.err); got != c.want {
				t.Errorf("neverReachedReceiver(%v) = %t, want %t", c.err, got, c.want)
			}
		})
	}
}

// The end-to-end anchor for the classification above: real transport errors,
// recorded the way a run would record them.
func TestUnansweredExportsAreSplitByWhetherTheyWereSent(t *testing.T) {
	// Takes the whole export, then drops the connection without answering: the
	// receiver holds the spans and the client will never learn their fate.
	silent := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			t.Error("the test server must let the handler drop the connection")
			return
		}
		connection, _, err := hijacker.Hijack()
		if err != nil {
			t.Errorf("hijacking the connection: %v", err)
			return
		}
		_ = connection.Close()
	}))
	t.Cleanup(silent.Close)

	const spans = 2

	cases := []struct {
		name        string
		target      string
		wantUnknown int
		wantNotSent int
	}{
		{
			name:        "the receiver took the request and never answered",
			target:      silent.URL,
			wantUnknown: spans,
		},
		{
			name:        "the receiver could not be dialled",
			target:      "http://" + closedAddress(t),
			wantNotSent: spans,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			recorder := NewRecorder()
			proxy := NewProxy(Options{
				Target:         c.target,
				Recorder:       recorder,
				Logger:         slog.New(slog.DiscardHandler),
				RequestTimeout: 200 * time.Millisecond,
				Connections:    1,
			})

			body := marshalExport(t, &collectorpb.ExportTraceServiceRequest{
				ResourceSpans: []*tracepb.ResourceSpans{{ScopeSpans: []*tracepb.ScopeSpans{
					{Spans: []*tracepb.Span{{TraceId: []byte("0123456789abcdef")}, {TraceId: []byte("0123456789abcdef")}}},
				}}},
			})
			proxy.ServeHTTP(httptest.NewRecorder(),
				httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body)))

			got := recorder.Result()
			if got.UnknownSpans != c.wantUnknown {
				t.Errorf("UnknownSpans = %d, want %d", got.UnknownSpans, c.wantUnknown)
			}
			if got.NotSentSpans != c.wantNotSent {
				t.Errorf("NotSentSpans = %d, want %d", got.NotSentSpans, c.wantNotSent)
			}
			// Spans that went out stay offered; spans that never left do not.
			if got.SpansOffered != c.wantUnknown {
				t.Errorf("SpansOffered = %d, want %d", got.SpansOffered, c.wantUnknown)
			}
			if got.SpansAccepted != 0 {
				t.Errorf("SpansAccepted = %d, want 0: nothing was acknowledged", got.SpansAccepted)
			}
			if got.AnsweredRequests() != 0 {
				t.Errorf("AnsweredRequests() = %d, want 0", got.AnsweredRequests())
			}
			if got.Latency.Max != 0 {
				t.Errorf("Latency.Max = %s, want none: an unanswered request has no latency", got.Latency.Max)
			}
		})
	}
}

// closedAddress is a loopback address nothing is listening on, so dialling it
// fails outright rather than hanging.
func closedAddress(t *testing.T) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserving a port: %v", err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatalf("releasing the reserved port: %v", err)
	}
	return address
}

func marshalExport(t *testing.T, request *collectorpb.ExportTraceServiceRequest) []byte {
	t.Helper()
	body, err := proto.Marshal(request)
	if err != nil {
		t.Fatalf("marshalling test export: %v", err)
	}
	return body
}
