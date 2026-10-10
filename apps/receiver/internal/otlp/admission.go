package otlp

import (
	"context"
	"errors"
	"sync/atomic"
	"time"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/tap"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

// Admission bounds how many export bodies this receiver holds at once.
//
// MaxDecodedBodyBytes bounds one export; nothing bounded how many. Measured on
// the development receiver: eight keyless gRPC exports, each 15 MiB decoded
// from a few KiB on the wire, raised it from 262 MiB to 629 MiB of its 768 MiB
// before every one was refused for having no key — grpc-go reads and
// decompresses a message before any handler runs (docs/changes/
// ingest-cannot-be-knocked-over).
//
// Two pools. The main one is shared by both doors and both transports, so the
// receiver's worst case is one number. The unverified one is small and holds
// only gRPC exports whose key this process has never seen: a stranger rotating
// made-up keys can fill it, and nothing else.
type Admission struct {
	main       slots
	unverified slots
}

// NewAdmission returns the budget for one receiver process.
func NewAdmission(maxInflight, maxUnverified int) *Admission {
	a := &Admission{}
	a.main.limit = int64(maxInflight)
	a.unverified.limit = int64(maxUnverified)
	return a
}

// exportBudget is the longest one admitted export may hold its slot. The HTTP
// door's own read and write timeouts, so a slow sender costs the same on
// either transport — and, on gRPC, does not hold a slot forever by trickling a
// body that grpc-go would otherwise wait for indefinitely.
const exportBudget = readTimeout + writeTimeout

// busyRetryAfter is the delay a refused-for-load exporter is told to wait.
// Short: the condition is a burst, and a held slot frees within exportBudget.
const busyRetryAfter = time.Second

const messageBusy = "receiver is at its in-flight export limit, retry"

type slots struct {
	held  atomic.Int64
	limit int64
}

func (s *slots) take() bool {
	for {
		held := s.held.Load()
		if held >= s.limit {
			return false
		}
		if s.held.CompareAndSwap(held, held+1) {
			return true
		}
	}
}

func (s *slots) give() { s.held.Add(-1) }

func busy() *refusal {
	return refuseUntil(code.Code_RESOURCE_EXHAUSTED, messageBusy, busyRetryAfter)
}

// admit takes a main-pool slot for an export whose key has been verified,
// before its body is read. The caller must call release once the export is
// answered.
func (a *Admission) admit() (release func(), refused *refusal) {
	if !a.main.take() {
		return nil, busy()
	}
	return a.main.give, nil
}

// Screener is what the gRPC door's tap needs of its credential door: a verdict
// from memory alone.
type Screener interface {
	Screen(presented string) auth.Verdict
}

// tap returns grpc-go's in-tap handle for the gRPC door: the one hook that runs
// before a stream's body is read.
//
// grpc-go runs it on the connection's I/O goroutine, so it must not block —
// which is why it screens rather than authenticates. Every export it admits is
// still authenticated in full by runExport.
//
// A slot is released when the stream's context ends, which is every way a
// stream can end: answered, reset, timed out, or aborted by grpc-go before any
// handler ran. A release tied to the RPC's End event instead would leak on that
// last path.
//
// methods are the full method names this door serves, with the unit each
// counts; anything else is refused here, before a body is read.
func (a *Admission) tap(opts Options, screen Screener, methods map[string]Unit) tap.ServerInHandle {
	return func(ctx context.Context, info *tap.Info) (context.Context, error) {
		unit, served := methods[info.FullMethodName]
		if !served {
			return ctx, status.Error(codes.Unimplemented, "unknown method")
		}

		stamped := withCaller(withStart(ctx, time.Now()), callerOfRPC(ctx))
		var pool *slots
		switch screen.Screen(firstValue(info.Header.Get(auth.HeaderName))) {
		case auth.VerdictRefused:
			refused := refuse(code.Code_UNAUTHENTICATED, messageUnauthenticated).
				because(errors.New("refused before the body was read"))
			opts.record(stamped, info.FullMethodName, unit, itemsNotRead, refused)
			return ctx, refused.grpcError()
		case auth.VerdictKnown:
			pool = &a.main
		default:
			pool = &a.unverified
		}

		if !pool.take() {
			refused := busy()
			opts.record(stamped, info.FullMethodName, unit, itemsNotRead, refused)
			return ctx, refused.grpcError()
		}
		bounded, cancel := context.WithTimeout(ctx, exportBudget)
		context.AfterFunc(bounded, func() {
			cancel()
			pool.give()
		})
		return bounded, nil
	}
}

func firstValue(values []string) string {
	if len(values) == 0 {
		return ""
	}
	return values[0]
}
