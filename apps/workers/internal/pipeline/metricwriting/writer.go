package metricwriting

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// jsonAsStringSetting lets a JSON column travel the native protocol as one JSON
// document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// writer inserts metric blocks into ClickHouse.
type writer struct {
	pool *chpool.Pool
}

func newWriter(pool *chpool.Pool) *writer { return &writer{pool: pool} }

// insert writes one block as a columnar batch, and writes nothing at all when
// the block is empty — a metric that carried no data points must leave no row
// behind, not a row of zeros.
//
// It returns an error rather than retrying: the caller holds the Kafka offsets
// and commits only once this has succeeded, so a failure is retried by
// re-consuming.
func (w *writer) insert(ctx context.Context, table string, rows int, columns proto.Input) error {
	if rows == 0 {
		return nil
	}
	query := ch.Query{
		Body:  columns.Into(table),
		Input: columns,
		Settings: []ch.Setting{
			{Key: jsonAsStringSetting, Value: "1"},
		},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d rows into %s: %w", rows, table, err))
	}
	return nil
}
