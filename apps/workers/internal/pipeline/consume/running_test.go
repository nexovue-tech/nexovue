package consume

import (
	"context"
	"testing"
)

// The failure this guards against presented as a green container doing no work:
// ClickHouse was OOM-killed, the consume loop ended, and the worker went on
// serving a healthy /readyz for nine minutes while ingest was silently lost.
// Readiness has to answer "is this worker doing its job", not "are its
// dependencies reachable".
func TestReadinessFailsWhenTheLoopIsNotRunning(t *testing.T) {
	loop := &Loop[bool]{settings: Settings{Name: "trace-assembly"}}

	if err := loop.Running(context.Background()); err == nil {
		t.Fatal("a pipeline that has never started reported ready")
	}

	loop.running.Store(true)
	if err := loop.Running(context.Background()); err != nil {
		t.Fatalf("a running pipeline reported not ready: %v", err)
	}

	// The case that actually happened: it ran, then stopped.
	loop.running.Store(false)
	if err := loop.Running(context.Background()); err == nil {
		t.Fatal("a stopped pipeline reported ready; this is the nine-minute outage")
	}
}
