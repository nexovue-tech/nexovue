package logwriting

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
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
		return fmt.Errorf("insert %d logs: %w", block.rows(), err)
	}
	return nil
}
