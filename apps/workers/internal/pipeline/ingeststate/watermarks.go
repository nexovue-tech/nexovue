package ingeststate

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// ingestWrittenTable holds the log and metric watermarks, keyed the same way
// the span table is (migration 000029). One table for both signals: the key
// carries the topic, and the two pipelines consume different topics.
const ingestWrittenTable = "ingest_written_offsets"

// writtenThroughColumn is the watermark itself, named once so the read and the
// write cannot drift apart.
const writtenThroughColumn = "written_through"

// watermarks reads and writes one KeeperMap table of per-partition offsets.
//
// Three tables now hold this same two-column shape, so the access is written
// once and pointed at a table name rather than copied per signal.
type watermarks struct {
	pool  *chpool.Pool
	table string
}

// load reads every partition's watermark from the table.
func (w watermarks) load(ctx context.Context) (Written, error) {
	var (
		key     proto.ColStr
		through proto.ColInt64
	)

	found := make(Written)
	query := ch.Query{
		Body: "SELECT " + partitionKeyColumn + ", " + writtenThroughColumn + " FROM " + w.table,
		Result: proto.Results{
			{Name: partitionKeyColumn, Data: &key},
			{Name: writtenThroughColumn, Data: &through},
		},
		OnResult: func(_ context.Context, block proto.Block) error {
			for row := range block.Rows {
				partition, err := ParsePartitionKey(key.Row(row))
				if err != nil {
					return err
				}
				found[partition] = through[row]
			}
			return nil
		},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return nil, fmt.Errorf("read %s: %w", w.table, err)
	}
	return found, nil
}

// save records how far each partition has been written, overwriting whatever
// was there. One insert for every partition that moved, not one per partition.
func (w watermarks) save(ctx context.Context, written Written) error {
	if len(written) == 0 {
		return nil
	}

	var (
		key     proto.ColStr
		through proto.ColInt64
	)
	for partition, offset := range written {
		key.Append(partition.Key())
		through.Append(offset)
	}

	input := proto.Input{
		{Name: partitionKeyColumn, Data: &key},
		{Name: writtenThroughColumn, Data: &through},
	}
	query := ch.Query{
		Body:     input.Into(w.table),
		Input:    input,
		Settings: []ch.Setting{{Key: strictModeSetting, Value: "0"}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("write %d rows to %s: %w", len(written), w.table, err)
	}
	return nil
}
