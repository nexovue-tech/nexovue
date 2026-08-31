package ingeststate

import (
	"context"
	"sync"

	"github.com/ClickHouse/ch-go/chpool"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// WatermarkStore is the durable home of one watermark per partition. KeeperMap
// is the implementation; the interface is what lets the guard be tested without
// a cluster.
type WatermarkStore interface {
	// Load reads every partition's watermark.
	Load(ctx context.Context) (Written, error)

	// Save records how far the named partitions have been written.
	Save(ctx context.Context, written Written) error
}

// NewIngestWatermarks returns the durable store the log and metric pipelines
// share (migration 000029). The caller owns the pool.
func NewIngestWatermarks(pool *chpool.Pool) WatermarkStore {
	return keeperWatermarks{watermarks{pool: pool, table: ingestWrittenTable}}
}

// keeperWatermarks adapts the unexported table access to the exported
// interface, so the two pipelines depend on the interface rather than the
// table.
type keeperWatermarks struct{ table watermarks }

func (k keeperWatermarks) Load(ctx context.Context) (Written, error) {
	return k.table.load(ctx)
}

func (k keeperWatermarks) Save(ctx context.Context, written Written) error {
	return k.table.save(ctx, written)
}

// WriteGuard makes a pipeline that inserts inside Absorb idempotent across a
// restart: it remembers how far each partition's rows have been acknowledged by
// the store, and recognises a replayed record as one whose rows are already
// there.
//
// It is the whole of what these pipelines need, and much less than Guard. There
// is no counter to be wrong twice and no buffer between a record and its rows,
// so there is one watermark, no BEFORE/AFTER bracket and no scenario to
// classify — a batch's offsets map straight onto the rows it produced.
type WriteGuard struct {
	store WatermarkStore

	// Invalidate is called from the rebalance callback while the pipeline
	// goroutine is inside its poll, so every field below is held under the lock.
	mu      sync.Mutex
	written map[Partition]int64
	stale   bool
}

// NewWriteGuard returns a guard whose cache is empty and due a read. It does
// not touch the store: a store that is down at boot must show up as a red
// readiness probe, not as a crash loop.
func NewWriteGuard(store WatermarkStore) *WriteGuard {
	return &WriteGuard{store: store, written: make(map[Partition]int64), stale: true}
}

// Invalidate marks the cache due a re-read. The consumer group's assignment
// callback calls it: a partition can arrive from another worker, whose writes
// this process never saw, and writing rows on a stale cache is what duplicates
// them.
func (g *WriteGuard) Invalidate() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.stale = true
}

// Refresh re-reads the watermarks when they are due, and is a no-op otherwise.
func (g *WriteGuard) Refresh(ctx context.Context) error {
	g.mu.Lock()
	due := g.stale
	g.mu.Unlock()
	if !due {
		return nil
	}

	written, err := g.store.Load(ctx)
	if err != nil {
		return err
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	// Whole-cache replacement is safe because nothing is cached until the store
	// has accepted it, so a read can only return what this worker wrote or
	// something newer from the worker that held the partition before it.
	g.written, g.stale = written, false
	return nil
}

// Storing reports whether this record's rows still have to be written. A
// partition nothing is known about is written: a duplicate row can be collapsed
// later, and a missing one cannot.
func (g *WriteGuard) Storing(partition Partition, offset int64) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	through, known := g.written[partition]
	return !known || offset > through
}

// Advance records how far the store has acknowledged, for the partitions the
// given positions actually move past. It must be called only after the insert
// returns.
//
// Writing a watermark before the acknowledgement is the one ordering that
// loses data: it would make a replay skip rows nothing ever wrote. After it,
// the worst a lost write costs is the duplicate this exists to prevent.
func (g *WriteGuard) Advance(ctx context.Context, covered []consume.Position) error {
	moved := g.beyondCache(RangesOf(covered))
	if len(moved) == 0 {
		return nil
	}
	if err := g.store.Save(ctx, moved); err != nil {
		return err
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	for partition, offset := range moved {
		g.written[partition] = offset
	}
	return nil
}

// beyondCache narrows per-partition ranges to the partitions whose watermark
// actually advances, so a turn of the loop that stored nothing new writes
// nothing.
func (g *WriteGuard) beyondCache(reached map[Partition]Range) Written {
	g.mu.Lock()
	defer g.mu.Unlock()

	moved := make(Written, len(reached))
	for partition, offsets := range reached {
		if through, known := g.written[partition]; known && offsets.Max <= through {
			continue
		}
		moved[partition] = offsets.Max
	}
	return moved
}

// WrittenThrough reports one partition's cached watermark, which is what a log
// line and a test read.
func (g *WriteGuard) WrittenThrough(partition Partition) (int64, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	through, known := g.written[partition]
	return through, known
}
