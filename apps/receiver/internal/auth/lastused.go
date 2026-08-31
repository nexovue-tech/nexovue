package auth

import (
	"context"
	"time"
)

// Recording when a key last authenticated, without putting a write on the hot
// path.
//
// Two things keep the cost near zero.
//
// **It is throttled by the credential cache, not by a clock of its own.** A use
// is recorded only when the credential came from a fresh Postgres read — that is,
// on a cache miss — so a key in continuous use is written at most once per cache
// TTL per receiver, and a cached hit writes nothing at all. The timestamp is
// therefore coarse by construction, accurate to within the TTL, which is what
// "has this key gone quiet?" actually needs.
//
// **The write never happens inside the request.** Record hands the prefix to a
// buffered channel and returns; a single goroutine drains it. A slow or
// unreachable control plane costs an export nothing, which matches how the rest
// of this path treats its dependencies — losing a customer's telemetry costs more
// than losing a piece of bookkeeping.
//
// The write is also gated on the hash comparison passing. Recording at lookup
// time would be cheaper still — the refresh statement could carry the update —
// but then anyone who guessed a live prefix could keep a key looking busy, and a
// customer checking that the old key has gone quiet before revoking it would be
// reading an attacker's answer rather than their own traffic.

// KeyUseRecorder is told that a key authenticated. Implementations must not
// block: this is called from the request path.
type KeyUseRecorder interface {
	Record(keyPrefix string, at time.Time)
}

// LastUsedWriter persists the timestamp. Separate from KeyReader because reading
// a credential and recording a use have different failure consequences: a failed
// read refuses an export, a failed write loses a timestamp.
type LastUsedWriter interface {
	MarkUsed(ctx context.Context, keyPrefix string, at time.Time) error
}

// pendingUses bounds the queue. One entry per live key per cache TTL means a
// full buffer says the writer has stalled, and in that state the write would not
// have landed anyway.
const pendingUses = 256

// lastUsedWriteTimeout bounds one update, so a stuck connection cannot hold the
// drainer for the life of the process.
const lastUsedWriteTimeout = 5 * time.Second

type keyUse struct {
	prefix string
	at     time.Time
}

// LastUsedRecorder queues key uses and writes them from one goroutine.
type LastUsedRecorder struct {
	writer LastUsedWriter
	queue  chan keyUse
}

// NewLastUsedRecorder returns a recorder that does nothing until Run is called.
func NewLastUsedRecorder(writer LastUsedWriter) *LastUsedRecorder {
	return &LastUsedRecorder{writer: writer, queue: make(chan keyUse, pendingUses)}
}

// Record queues one use. It never blocks: a full queue drops the timestamp,
// because a delayed export is a worse outcome than a stale "last used" reading.
func (r *LastUsedRecorder) Record(keyPrefix string, at time.Time) {
	select {
	case r.queue <- keyUse{prefix: keyPrefix, at: at}:
	default:
	}
}

// Run drains the queue until ctx is cancelled. Call it once, in a goroutine.
//
// Whatever is queued at shutdown is abandoned rather than flushed: it is at most
// a few seconds of imprecision on a timestamp that is already coarse, and it is
// not worth holding a drain open against a database that may be the reason we
// are shutting down.
func (r *LastUsedRecorder) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case use := <-r.queue:
			r.write(ctx, use)
		}
	}
}

func (r *LastUsedRecorder) write(ctx context.Context, use keyUse) {
	writeCtx, cancel := context.WithTimeout(ctx, lastUsedWriteTimeout)
	defer cancel()
	// The error is deliberately dropped. There is no caller to tell and nothing
	// to retry: the next use of this key queues a newer timestamp anyway.
	_ = r.writer.MarkUsed(writeCtx, use.prefix, use.at)
}

// noKeyUseRecorder is what an Authenticator built without one uses, so the
// request path has no nil check in it.
type noKeyUseRecorder struct{}

func (noKeyUseRecorder) Record(string, time.Time) {}
