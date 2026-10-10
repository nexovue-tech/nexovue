package otlp

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/tap"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

type fixedScreen auth.Verdict

func (f fixedScreen) Screen(string) auth.Verdict { return auth.Verdict(f) }

const tracesMethod = "/opentelemetry.proto.collector.trace.v1.TraceService/Export"

func tapUnderTest(a *Admission, verdict auth.Verdict) tap.ServerInHandle {
	opts := Options{Logger: slog.New(slog.NewTextHandler(io.Discard, nil)), Traffic: NewTraffic()}
	return a.tap(opts, fixedScreen(verdict), map[string]Unit{tracesMethod: Spans})
}

func tapInfo(method string) *tap.Info {
	return &tap.Info{FullMethodName: method, Header: metadata.Pairs(auth.HeaderName, "presented")}
}

func codeOf(err error) codes.Code { return status.Code(err) }

func TestTapRefusesAMethodTheDoorDoesNotServe(t *testing.T) {
	// Refused here, so every admitted stream is an export grpc-go will run to
	// the end — the property every held slot relies on to come back.
	a := NewAdmission(1, 1)
	_, err := tapUnderTest(a, auth.VerdictKnown)(t.Context(), tapInfo("/grpc.health.v1.Health/Check"))
	if codeOf(err) != codes.Unimplemented {
		t.Fatalf("code = %v, want Unimplemented", codeOf(err))
	}
	if a.main.held.Load() != 0 {
		t.Error("a refused method took a slot")
	}
}

func TestTapRefusesAKeyItCanAlreadyJudge(t *testing.T) {
	a := NewAdmission(1, 1)
	_, err := tapUnderTest(a, auth.VerdictRefused)(t.Context(), tapInfo(tracesMethod))
	if codeOf(err) != codes.Unauthenticated {
		t.Fatalf("code = %v, want Unauthenticated", codeOf(err))
	}
	if a.main.held.Load()+a.unverified.held.Load() != 0 {
		t.Error("a refused key took a slot")
	}
}

func TestTapPutsEachVerdictInItsOwnPool(t *testing.T) {
	a := NewAdmission(1, 1)
	if _, err := tapUnderTest(a, auth.VerdictKnown)(t.Context(), tapInfo(tracesMethod)); err != nil {
		t.Fatal(err)
	}
	if _, err := tapUnderTest(a, auth.VerdictUnseen)(t.Context(), tapInfo(tracesMethod)); err != nil {
		t.Fatal(err)
	}
	if a.main.held.Load() != 1 || a.unverified.held.Load() != 1 {
		t.Fatalf("held main=%d unverified=%d, want 1 and 1", a.main.held.Load(), a.unverified.held.Load())
	}
	// A stranger cannot spend a customer's room, nor the reverse.
	if _, err := tapUnderTest(a, auth.VerdictUnseen)(t.Context(), tapInfo(tracesMethod)); codeOf(err) != codes.ResourceExhausted {
		t.Errorf("second unseen export: code = %v, want ResourceExhausted", codeOf(err))
	}
	if _, err := tapUnderTest(a, auth.VerdictKnown)(t.Context(), tapInfo(tracesMethod)); codeOf(err) != codes.ResourceExhausted {
		t.Errorf("second known export: code = %v, want ResourceExhausted", codeOf(err))
	}
}

func TestTapReleasesTheSlotWhenTheStreamEnds(t *testing.T) {
	a := NewAdmission(1, 1)
	stream, end := context.WithCancel(t.Context())
	admitted, err := tapUnderTest(a, auth.VerdictKnown)(stream, tapInfo(tracesMethod))
	if err != nil {
		t.Fatal(err)
	}
	if _, bounded := admitted.Deadline(); !bounded {
		t.Error("an admitted stream has no deadline, so a slow sender could hold its slot forever")
	}

	end() // grpc-go cancels the stream's context on every way a stream ends
	waitUntil(t, func() bool { return a.main.held.Load() == 0 })
}

func TestAdmitRefusesBeyondTheBudgetAndReleases(t *testing.T) {
	a := NewAdmission(1, 1)
	release, refused := a.admit()
	if refused != nil {
		t.Fatal(refused.message)
	}
	if _, again := a.admit(); again == nil || again.retryAfter <= 0 {
		t.Fatal("an export past the budget must be refused with a retry delay")
	}
	release()
	if _, refused := a.admit(); refused != nil {
		t.Error("a released slot was not given back")
	}
}

func waitUntil(t *testing.T, done func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !done() {
		if time.Now().After(deadline) {
			t.Fatal("condition not reached within 2s")
		}
		time.Sleep(5 * time.Millisecond)
	}
}
