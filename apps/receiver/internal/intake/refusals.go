// Package intake records the minutes this receiver refused telemetry because the
// broker would not take it (F6, C5; design revision 2.1, item 1).
//
// Detection reads them: a minute nexovue itself refused is incomplete for every
// project, so it is held rather than judged as a customer's traffic falling.
// Because one minute held this way holds every tenant's, what is recorded is
// narrow on purpose. The door decides what counts (a broker-level failure on a
// request its sender had not ended); this package decides how much: at least
// MinRefusalsPerMinute in the minute, written once the minute is over.
//
// It is modelled on `auth/lastused.go`: the request path only counts in memory,
// and one goroutine writes. A slow or unreachable control plane costs an export
// nothing; a minute it could not record is the cost.
package intake

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// ReasonBrokerUnavailable is the one reason `intake_refusals` admits (its CHECK,
// migration 0041), and the door's tally name for the same refusals.
const ReasonBrokerUnavailable = "broker_unavailable"

// MinRefusalsPerMinute is the fewest refusals that make a minute nexovue's
// outage rather than a request or two that failed (design 2.1, item 1).
const MinRefusalsPerMinute = 5

const (
	// flushInterval is how soon after a minute ends it is written. The scheduler
	// settles a minute 3 minutes after it ends, so seconds are enough.
	flushInterval = 10 * time.Second

	// keptUnwritten is how long a minute waits for the control plane to take it.
	// It matches the scheduler's hold limit: an older minute would hold nothing.
	keptUnwritten = 30 * time.Minute

	// writeTimeout bounds one upsert, so a stuck connection cannot hold the
	// writer for the life of the process.
	writeTimeout = 5 * time.Second
)

// Writer persists one minute's count for this receiver.
type Writer interface {
	RecordRefusals(ctx context.Context, minute time.Time, receiverID, reason string, count int) error
}

// Recorder counts refusals per minute and writes each finished minute that had
// enough of them.
type Recorder struct {
	writer     Writer
	receiverID string
	flushEvery time.Duration
	log        *slog.Logger

	mu sync.Mutex
	// minutes holds at most keptUnwritten's worth of entries: older ones are
	// dropped at each flush, and the clock is the only key.
	minutes map[time.Time]int
	// failing are the minutes whose write has failed and been logged, so a
	// control plane that stays down is one line per minute, not one per flush.
	// Only the flush goroutine touches it.
	failing map[time.Time]bool
}

// NewRecorder returns a recorder that writes nothing until Run is called.
// receiverID names this receiver in the table's key.
func NewRecorder(writer Writer, receiverID string, log *slog.Logger) *Recorder {
	return &Recorder{
		writer:     writer,
		receiverID: receiverID,
		flushEvery: flushInterval,
		log:        log,
		minutes:    make(map[time.Time]int),
		failing:    make(map[time.Time]bool),
	}
}

// Refused counts one refusal in the minute it happened. Called from the request
// path, so it only takes a lock and adds.
func (r *Recorder) Refused(at time.Time) {
	minute := at.UTC().Truncate(time.Minute)
	r.mu.Lock()
	r.minutes[minute]++
	r.mu.Unlock()
}

// Run writes finished minutes until ctx is cancelled. Call it once, in a
// goroutine. What is unwritten at shutdown is abandoned, as `LastUsedRecorder`
// abandons its queue: the control plane may be why the process is stopping.
func (r *Recorder) Run(ctx context.Context) {
	ticker := time.NewTicker(r.flushEvery)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			r.flush(ctx, now)
		}
	}
}

// flush writes every minute that has ended and had enough refusals, keeps any
// whose write failed for the next flush, and forgets the rest.
func (r *Recorder) flush(ctx context.Context, now time.Time) {
	current := now.UTC().Truncate(time.Minute)
	for minute, count := range r.finished(current) {
		if count < MinRefusalsPerMinute || current.Sub(minute) > keptUnwritten {
			delete(r.failing, minute)
			continue
		}
		if err := r.write(ctx, minute, count); err != nil {
			r.reportFailure(ctx, minute, err)
			r.keep(minute, count)
			continue
		}
		delete(r.failing, minute)
	}
}

// reportFailure logs a minute's first failed write. Detection cannot hold a
// minute it never hears of, so a silent failure would let nexovue's own outage
// read as every customer's traffic falling.
func (r *Recorder) reportFailure(ctx context.Context, minute time.Time, err error) {
	if r.failing[minute] {
		return
	}
	r.failing[minute] = true
	r.log.ErrorContext(ctx, "could not record a minute of refused exports; retrying until it is too old to hold",
		slog.Time("minute", minute),
		slog.Duration("retried_for", keptUnwritten),
		slog.Any("error", fmt.Errorf("record the refusals of %s: %w", minute.Format(time.RFC3339), err)),
	)
}

// finished takes the minutes before `current` out of the count.
func (r *Recorder) finished(current time.Time) map[time.Time]int {
	r.mu.Lock()
	defer r.mu.Unlock()
	done := make(map[time.Time]int)
	for minute, count := range r.minutes {
		if minute.Before(current) {
			done[minute] = count
			delete(r.minutes, minute)
		}
	}
	return done
}

// keep returns a minute whose write failed, adding to anything counted since.
func (r *Recorder) keep(minute time.Time, count int) {
	r.mu.Lock()
	r.minutes[minute] += count
	r.mu.Unlock()
}

func (r *Recorder) write(ctx context.Context, minute time.Time, count int) error {
	writeCtx, cancel := context.WithTimeout(ctx, writeTimeout)
	defer cancel()
	return r.writer.RecordRefusals(writeCtx, minute, r.receiverID, ReasonBrokerUnavailable, count)
}
