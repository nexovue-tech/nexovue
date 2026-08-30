package logwriting

import (
	"context"
	"errors"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// jsonAsStringSetting lets a JSON column travel the native protocol as one
// JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// logWriter inserts blocks of log records into ClickHouse.
type logWriter struct {
	pool *chpool.Pool
}

func newLogWriter(pool *chpool.Pool) *logWriter {
	return &logWriter{pool: pool}
}

// insert writes the block as one columnar batch. It returns an error rather
// than retrying: the caller holds the Kafka offsets and only commits once this
// has succeeded, so a failure is retried by re-consuming rather than in here.
func (w *logWriter) insert(ctx context.Context, block *logBlock) error {
	if block.rows() == 0 {
		return nil
	}
	query := ch.Query{
		Body:  block.statement(),
		Input: block.columns,
		Settings: []ch.Setting{
			{Key: jsonAsStringSetting, Value: "1"},
		},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		wrapped := fmt.Errorf("insert %d logs: %w", block.rows(), err)
		if refusedTheData(err) {
			return consume.AsPermanent(wrapped)
		}
		return wrapped
	}
	return nil
}

// refusedTheData reports whether ClickHouse rejected the rows themselves rather
// than being unable to take them right now.
//
// Deliberately a short list. Misjudging an outage as permanent parks records
// that would have been written a second later, so anything not clearly about
// the data's own shape stays retryable — the default is to wait.
func refusedTheData(err error) bool {
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
