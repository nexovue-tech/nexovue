package consume

import (
	"context"
	"fmt"
	"log/slog"
	"time"
)

// Retry budget for a call out of the process.
//
// A broker or store that is briefly unreachable is the normal weather of a
// distributed system, not a reason to end the worker. Nothing is lost by
// waiting: offsets stay uncommitted, so the records are still on the broker,
// and exiting would only discard whatever is in memory and re-consume them
// anyway. Past the budget the outage is no longer brief, and ending the process
// is the honest answer — a restart at least re-dials from scratch.
const (
	retryBudget      = 2 * time.Minute
	retryFirstDelay  = 250 * time.Millisecond
	retryMaxDelay    = 5 * time.Second
	retryDelayFactor = 2
)

// externalCallTimeout bounds every call the loop makes out of the process.
//
// Without it a connection killed underneath the pool never returns: chpool dials
// with a timeout but an established socket that dies has none, so an insert after
// a ClickHouse restart blocks forever. The loop then stops consuming while the
// process stays up, the goroutine stays live, and readiness stays green — a wedge
// that reports healthy, which is worse than a crash because nothing restarts it.
// Bounded, the same failure surfaces as an error, and an error exits the process.
const externalCallTimeout = 30 * time.Second

// bounded returns a context for one call out of the process. The caller must
// cancel it.
func bounded(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, externalCallTimeout)
}

// Retry runs op until it succeeds, the budget runs out, or the worker is asked
// to stop. Every attempt is bounded, so a call that never returns is a failure
// rather than a wedge.
//
// franz-go retries its own requests (RequestRetries, 20 by default, backing off
// 250ms–2.5s), so wrapping a Kafka call looks redundant. It is not: before this
// existed the pipeline died on `commit offsets: unable to dial: lookup kafka: no
// such host` during a twenty-second broker outage, which franz-go's own retries
// did not absorb. Measured, not assumed — do not remove it without reproducing
// that.
func (l *Loop[S]) Retry(ctx context.Context, what string, op func(context.Context) error) error {
	deadline := l.now().Add(retryBudget)
	delay := retryFirstDelay

	for attempt := 1; ; attempt++ {
		callCtx, cancel := bounded(ctx)
		err := op(callCtx)
		cancel()
		if err == nil {
			if attempt > 1 {
				l.log.InfoContext(ctx, "recovered after a transient failure",
					slog.String("operation", what),
					slog.Int("attempts", attempt),
				)
			}
			return nil
		}
		if ctx.Err() != nil {
			return err
		}
		// A store that refused the data will refuse it again. Spending the
		// budget on it only delays the answer and takes the worker down at the
		// end of it.
		if IsPermanent(err) {
			return err
		}
		if !l.now().Add(delay).Before(deadline) {
			return fmt.Errorf("%s failed for %s: %w", what, retryBudget, err)
		}

		l.log.WarnContext(ctx, "transient failure, retrying",
			slog.String("operation", what),
			slog.Int("attempt", attempt),
			slog.Duration("retry_in", delay),
			slog.String("error", err.Error()),
		)
		select {
		case <-ctx.Done():
			return err
		case <-time.After(delay):
		}
		if delay *= retryDelayFactor; delay > retryMaxDelay {
			delay = retryMaxDelay
		}
	}
}
