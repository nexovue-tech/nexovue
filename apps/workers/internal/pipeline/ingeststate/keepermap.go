package ingeststate

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// offsetsTable holds one row per partition, keyed by "{topic}:{partition}". It
// is a KeeperMap table (migration 000010), so its contents live in Keeper and
// outlive the ClickHouse process — which is the whole reason it exists.
const offsetsTable = "span_ingest_offsets"

// writtenTable holds the span watermark, keyed the same way (migration 000026).
// A second table rather than a column on the first because KeeperMap refuses
// ADD COLUMN; the migration records the measurement.
const writtenTable = "span_written_offsets"

// partitionKeyColumn is the primary key both tables are keyed by.
const partitionKeyColumn = "partition_key"

// strictModeSetting is off so that writing a partition's row again overwrites
// it. Named explicitly rather than left to the server default, because with the
// setting on every settlement after the first would throw.
const strictModeSetting = "keeper_map_strict_mode"

// KeeperMap reads and writes the per-partition state in ClickHouse.
type KeeperMap struct {
	pool  *chpool.Pool
	spans watermarks
}

// NewKeeperMap returns a store over an existing pool. The caller owns the pool.
func NewKeeperMap(pool *chpool.Pool) *KeeperMap {
	return &KeeperMap{pool: pool, spans: watermarks{pool: pool, table: writtenTable}}
}

// Load reads every partition's state from both tables and merges them.
//
// Two round trips, on the rebalance path only: the guard caches the result and
// re-reads it when the consumer group moves a partition, not per batch.
func (s *KeeperMap) Load(ctx context.Context) (map[Partition]State, error) {
	states, err := s.loadCounted(ctx)
	if err != nil {
		return nil, err
	}
	written, err := s.spans.load(ctx)
	if err != nil {
		return nil, err
	}
	for partition, offset := range written {
		// A partition can have a span watermark and no counter settlement yet;
		// it starts from a range that claims nothing rather than being dropped.
		state, counted := states[partition]
		if !counted {
			state = unsettled
		}
		state.WrittenThrough = offset
		states[partition] = state
	}
	return states, nil
}

// loadCounted reads every partition's counter state. The table holds one small
// row per partition, so reading all of it costs less than filtering it.
func (s *KeeperMap) loadCounted(ctx context.Context) (map[Partition]State, error) {
	var (
		key       proto.ColStr
		minOffset proto.ColInt64
		maxOffset proto.ColInt64
		flag      proto.ColStr
	)

	states := make(map[Partition]State)
	query := ch.Query{
		Body: "SELECT " + partitionKeyColumn + ", min_offset, max_offset, state FROM " + offsetsTable,
		Result: proto.Results{
			{Name: partitionKeyColumn, Data: &key},
			{Name: "min_offset", Data: &minOffset},
			{Name: "max_offset", Data: &maxOffset},
			{Name: "state", Data: &flag},
		},
		OnResult: func(_ context.Context, block proto.Block) error {
			for row := range block.Rows {
				partition, err := ParsePartitionKey(key.Row(row))
				if err != nil {
					return err
				}
				parsed, err := ParseFlag(flag.Row(row))
				if err != nil {
					return fmt.Errorf("partition %s: %w", partition.Key(), err)
				}
				states[partition] = State{
					Range:          Range{Min: minOffset[row], Max: maxOffset[row]},
					Flag:           parsed,
					WrittenThrough: NothingWritten,
				}
			}
			return nil
		},
	}
	if err := s.pool.Do(ctx, query); err != nil {
		return nil, fmt.Errorf("read %s: %w", offsetsTable, err)
	}
	return states, nil
}

// Save writes each partition's state, overwriting whatever was there.
func (s *KeeperMap) Save(ctx context.Context, states map[Partition]State) error {
	if len(states) == 0 {
		return nil
	}

	var (
		key       proto.ColStr
		minOffset proto.ColInt64
		maxOffset proto.ColInt64
		flag      proto.ColStr
	)
	for partition, state := range states {
		key.Append(partition.Key())
		minOffset.Append(state.Min)
		maxOffset.Append(state.Max)
		flag.Append(string(state.Flag))
	}

	input := proto.Input{
		{Name: partitionKeyColumn, Data: &key},
		{Name: "min_offset", Data: &minOffset},
		{Name: "max_offset", Data: &maxOffset},
		{Name: "state", Data: &flag},
	}
	query := ch.Query{
		Body:     input.Into(offsetsTable),
		Input:    input,
		Settings: []ch.Setting{{Key: strictModeSetting, Value: "0"}},
	}
	if err := s.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("write %d rows to %s: %w", len(states), offsetsTable, err)
	}
	return nil
}

// SaveWritten records how far each partition's spans have been written,
// overwriting whatever was there.
func (s *KeeperMap) SaveWritten(ctx context.Context, written Written) error {
	return s.spans.save(ctx, written)
}
