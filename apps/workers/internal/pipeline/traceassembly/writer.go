package traceassembly

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
)

// jsonAsStringSetting lets a JSON column travel the native protocol as one
// JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// spanWriter inserts blocks of spans into ClickHouse.
type spanWriter struct {
	pool *chpool.Pool
}

func newSpanWriter(pool *chpool.Pool) *spanWriter {
	return &spanWriter{pool: pool}
}

// insert writes the block as one columnar batch. It returns an error rather
// than retrying: the caller holds the Kafka offsets and only commits once this
// has succeeded, so a failure is retried by re-consuming rather than in here.
func (w *spanWriter) insert(ctx context.Context, block *spanBlock) error {
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
		return fmt.Errorf("insert %d spans: %w", block.rows(), err)
	}
	return nil
}
