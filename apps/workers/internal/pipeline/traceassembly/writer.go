package traceassembly

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// jsonAsStringSetting lets a JSON column travel the native protocol as one
// JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// dependentViewDeduplicationSetting pins the server default that lets a
// retried insert be deduplicated in the materialized views reading this table,
// rather than only in the table itself.
//
// It is set explicitly because we depend on it and a profile could turn it off
// without anything failing: the insert would still succeed and
// `span_trace_lookup` would quietly start double-counting retries. What
// actually does the deduplicating is the window on the view's target table
// (000022) — measured, and the reason that migration exists.
//
// `span_trace_lookup_mv` is the only view over this table since 000024 dropped
// `endpoint_stats_1m_mv`; the rollup is written by the derive stage now,
// before the sampler, and is deduplicated by its own insert.
const dependentViewDeduplicationSetting = "deduplicate_blocks_in_dependent_materialized_views"

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
			{Key: dependentViewDeduplicationSetting, Value: "1"},
		},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d spans: %w", block.rows(), err))
	}
	return nil
}
