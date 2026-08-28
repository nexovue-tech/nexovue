// Package clocktrust decides which clock a row is bucketed on.
//
// Every stage that puts rows into time buckets needs the same answer, and needs
// it to be the same answer. A span's timestamp comes from the customer's SDK,
// so it can be wrong in both directions, and each direction breaks something
// different:
//
//   - Too far ahead and the bucket never settles. Offsets are held until the
//     minute they fed has closed, so one span stamped a year out stops commits
//     for every partition that worker owns — silently, because spans keep
//     reaching storage and the pipeline keeps reporting healthy.
//   - Too far behind and the row lands outside retention and is deleted by the
//     next merge, which is data loss on something we acknowledged.
//
// The rule lived in `derive` and was copied into `serviceedges` the same day.
// Two copies of a bound whose whole job is to be a single agreed number is the
// shape of a defect waiting for one of them to be tuned.
package clocktrust

import "time"

// Bucket is the width of one time bucket. Counters and edges both aggregate per
// minute, and the future bound is expressed in terms of it.
const Bucket = time.Minute

const (
	// FutureSkew is one bucket width. Tolerance here is charged directly to
	// consumer lag — a batch's offsets wait for its bucket to close — so the
	// widest defensible value is the one that cannot cost more lag than the
	// bucketing already does.
	//
	// Prior art is looser: Prometheus rejects samples ten minutes ahead, and
	// Loki and Mimir default to a ten-minute creation grace period. Neither
	// pays for its tolerance in commit lag the way this pipeline does.
	FutureSkew = Bucket

	// PastSkew is the broker's own retention (deploy/kafka/topics.sh). Nothing
	// legitimately delivered can be older than the buffer it was replayed from,
	// and a replayed outage must still reach its real minutes — so the floor
	// must not cut into the window a replay can come from.
	//
	// Raising KAFKA_RETENTION_MS without raising this makes a legitimate replay
	// look like a broken clock.
	PastSkew = 7 * 24 * time.Hour
)

// Instant is the moment a row should be bucketed at, and the skew discarded to
// get there.
//
// A timestamp inside the trusted window is used as-is. Outside it, the row is
// attributed to when we received it — not clamped to the edge of the window,
// which would leave a bucket at the horizon and reproduce the wedge one bound
// further out; and not rejected, because a pre-sampling count is exact by
// invariant and dropping one would make it wrong.
//
// receivedAt is our own clock, from the envelope. When it is absent the row is
// judged against processingAt instead of going unjudged: an unjudged row can
// open a bucket arbitrarily far ahead, and a bucket that never settles stops
// commits for good.
//
// A non-zero discarded return is the caller's cue to say so — retiming that
// nothing reports is indistinguishable from a clock that was right.
func Instant(timestamp, receivedAt, processingAt time.Time) (at time.Time, discarded time.Duration) {
	reference := receivedAt
	if reference.IsZero() {
		reference = processingAt
	}
	skew := timestamp.Sub(reference)
	if skew > FutureSkew || skew < -PastSkew {
		return reference, skew
	}
	return timestamp, 0
}
