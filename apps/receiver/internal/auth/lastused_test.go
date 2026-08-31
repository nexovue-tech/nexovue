package auth

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"
)

// recordedUses stands in for the recorder so a test can assert what the request
// path handed it, without a goroutine or a database in the way.
type recordedUses struct {
	mu   sync.Mutex
	uses []keyUse
}

func (r *recordedUses) Record(prefix string, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.uses = append(r.uses, keyUse{prefix: prefix, at: at})
}

func (r *recordedUses) all() []keyUse {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]keyUse(nil), r.uses...)
}

func authenticatorRecordingInto(keys KeyReader, uses KeyUseRecorder) *Authenticator {
	return New(keys, Options{CacheTTL: time.Minute, CacheMaxEntries: 100, UseRecorder: uses})
}

// The whole point of the throttle: a key in continuous use costs one write per
// cache period, not one per request.
func TestAKeyInContinuousUseIsRecordedOncePerCachePeriod(t *testing.T) {
	uses := &recordedUses{}
	keys := seededKeys(validKey)
	auth := authenticatorRecordingInto(keys, uses)

	now := time.Now()
	auth.cache.now = func() time.Time { return now }

	for range 20 {
		if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
			t.Fatalf("Authenticate() = %v, want no error", err)
		}
	}
	if got := len(uses.all()); got != 1 {
		t.Fatalf("recorded %d uses across 20 requests, want 1: a cached hit must not write", got)
	}

	// Past the TTL the credential is re-read, and that read is the next write.
	now = now.Add(time.Minute + time.Second)
	if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if got := len(uses.all()); got != 2 {
		t.Errorf("recorded %d uses, want 2: an expired entry must record the use it refreshed", got)
	}
}

func TestARecordedUseNamesTheLookupPrefix(t *testing.T) {
	uses := &recordedUses{}
	auth := authenticatorRecordingInto(seededKeys(validKey), uses)

	if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}

	recorded := uses.all()
	if len(recorded) != 1 {
		t.Fatalf("recorded %d uses, want 1", len(recorded))
	}
	if recorded[0].prefix != validKey[:LookupPrefixLength] {
		t.Errorf("recorded prefix %q, want the lookup prefix", recorded[0].prefix)
	}
	// The secret half never leaves the request. A recorder that took the whole
	// key would put twenty-two secret characters into a queue and a log's reach.
	if strings.Contains(recorded[0].prefix, validKey[LookupPrefixLength:]) {
		t.Error("the recorded value carries the secret half of the key")
	}
}

// The gate that makes the timestamp trustworthy: if a guessed prefix could stamp
// last_used_at, a customer verifying the old key has gone quiet before revoking
// it would be reading the attacker instead of their own traffic.
func TestTheRightPrefixWithTheWrongSecretRecordsNothing(t *testing.T) {
	uses := &recordedUses{}
	auth := authenticatorRecordingInto(seededKeys(validKey), uses)
	impostor := validKey[:LookupPrefixLength] + strings.Repeat("z", keySecretLength-7)

	if _, err := auth.Authenticate(context.Background(), impostor); err == nil {
		t.Fatal("Authenticate() accepted the wrong secret")
	}
	if got := len(uses.all()); got != 0 {
		t.Errorf("recorded %d uses for an unverified key, want 0", got)
	}
}

func TestAnUnknownKeyRecordsNothing(t *testing.T) {
	uses := &recordedUses{}
	auth := authenticatorRecordingInto(seededKeys(validKey), uses)

	if _, err := auth.Authenticate(context.Background(), "nvk_live_absentabsentabsentabse"); err == nil {
		t.Fatal("Authenticate() accepted an unknown key")
	}
	if got := len(uses.all()); got != 0 {
		t.Errorf("recorded %d uses for an unknown key, want 0", got)
	}
}

func TestAnAuthenticatorWithNoRecorderStillAuthenticates(t *testing.T) {
	auth := New(seededKeys(validKey), Options{CacheTTL: time.Minute, CacheMaxEntries: 100})

	if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
}

/* ------------------------------------------------------- the recorder itself */

// spyWriter is the control-plane write, counted and released one at a time.
type spyWriter struct {
	written chan keyUse
	err     error
}

func (w *spyWriter) MarkUsed(_ context.Context, prefix string, at time.Time) error {
	w.written <- keyUse{prefix: prefix, at: at}
	return w.err
}

func TestTheRecorderWritesWhatItWasGiven(t *testing.T) {
	writer := &spyWriter{written: make(chan keyUse, 4)}
	recorder := NewLastUsedRecorder(writer)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go recorder.Run(ctx)

	at := time.Now()
	recorder.Record("nvk_live_present", at)

	select {
	case written := <-writer.written:
		if written.prefix != "nvk_live_present" || !written.at.Equal(at) {
			t.Errorf("wrote %+v, want the prefix and time that were recorded", written)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the recorder never wrote the use it was given")
	}
}

// Record is called from the request path, so it must return whether or not the
// writer is keeping up. A full queue drops rather than blocks.
func TestRecordNeverBlocksWhenTheWriterIsStalled(t *testing.T) {
	// A writer nothing drains: the first write parks in MarkUsed forever.
	recorder := NewLastUsedRecorder(&spyWriter{written: make(chan keyUse)})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go recorder.Run(ctx)

	returned := make(chan struct{})
	go func() {
		for range pendingUses * 3 {
			recorder.Record("nvk_live_present", time.Now())
		}
		close(returned)
	}()

	select {
	case <-returned:
	case <-time.After(2 * time.Second):
		t.Fatal("Record blocked once the queue filled; that would stall an export")
	}
}

func TestTheRecorderStopsWhenItsContextIsCancelled(t *testing.T) {
	recorder := NewLastUsedRecorder(&spyWriter{written: make(chan keyUse, 1)})

	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		recorder.Run(ctx)
		close(stopped)
	}()

	cancel()
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return after its context was cancelled")
	}
}
