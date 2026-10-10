// Package storefailure tells a ClickHouse error that will never succeed apart
// from one that merely failed now.
//
// It is separate from internal/pipeline/consume on purpose: that package's own
// note says it knows Kafka, not ClickHouse, and leaves what counts as permanent
// to whoever made the call. This is that judgement, in one place — every writer
// that inserts rows has to make it, and a second copy would be a copy free to
// be tuned on its own until two pipelines disagree about the same error.
package storefailure

import (
	"errors"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// RefusedTheData reports whether ClickHouse rejected the rows themselves rather
// than being unable to take them right now.
//
// Deliberately a short list. Misjudging an outage as permanent parks records
// that would have been written a second later, so anything not clearly about
// the data's own shape stays retryable — the default is to wait.
func RefusedTheData(err error) bool {
	var exception *ch.Exception
	if !errors.As(err, &exception) {
		return false
	}
	return exception.IsCode(
		proto.ErrIncorrectData,   // the rows themselves; what the demo's log record hit
		proto.ErrTypeMismatch,    // a value that will never fit its column
		proto.ErrIllegalColumn,   // e.g. a JSON path used where it cannot be
		proto.ErrCannotParseText, // malformed on the wire, not in flight
	)
}

// Judged returns an insert's error marked permanent when the store refused the
// data itself, and unchanged otherwise — so the retry loop gives up on a
// refusal at once and keeps waiting out an outage. Every writer that inserts
// rows returns through it.
func Judged(err error) error {
	if err != nil && RefusedTheData(err) {
		return consume.AsPermanent(err)
	}
	return err
}
