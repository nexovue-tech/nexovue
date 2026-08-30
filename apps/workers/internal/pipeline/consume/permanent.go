package consume

import "errors"

// Permanent marks an error that will never succeed however long we wait.
//
// The retry budget exists for the normal weather of a distributed system — a
// broker or store briefly unreachable — and waiting costs nothing there, because
// offsets stay uncommitted and the records are still on the broker. That
// reasoning inverts when the store has *rejected the data itself*. Waiting
// cannot help, the budget runs out, the pipeline ends, and every other pipeline
// in the process dies with it.
//
// Measured: one malformed log record from the OpenTelemetry Demo was retried 25
// times over two minutes and then stopped ingest for every tenant, across every
// signal, for an hour.
//
// What counts as permanent is decided by the package that made the call, not
// here — this package knows Kafka, not ClickHouse.
type Permanent struct{ Err error }

func (p *Permanent) Error() string { return p.Err.Error() }
func (p *Permanent) Unwrap() error { return p.Err }

// AsPermanent wraps an error the caller has judged unrecoverable.
func AsPermanent(err error) error { return &Permanent{Err: err} }

// IsPermanent reports whether retrying err could ever produce a different answer.
func IsPermanent(err error) bool {
	var permanent *Permanent
	return errors.As(err, &permanent)
}
