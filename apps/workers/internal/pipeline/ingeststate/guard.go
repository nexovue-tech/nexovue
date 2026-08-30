package ingeststate

import (
	"context"
	"sync"
)

// Store is the durable home of the per-partition state. KeeperMap is the
// implementation; the interface is what lets the state machine be tested
// without a cluster.
//
// Two writes rather than one because the two watermarks live in two KeeperMap
// tables. They would have been one row, but KeeperMap refuses ADD COLUMN and
// recreating the counter table would throw away the watermarks it holds — see
// migration 000026.
type Store interface {
	// Load reads both watermarks for every partition, merged into one state.
	Load(ctx context.Context) (map[Partition]State, error)

	// Save records the counter half: the settled range and how far it got.
	Save(ctx context.Context, states map[Partition]State) error

	// SaveWritten records the span half: how far each partition's spans have
	// been acknowledged by the store.
	SaveWritten(ctx context.Context, written Written) error
}

// unsettled is a partition nothing is yet known about: an empty counted range,
// which resolves to "count everything", and no spans written.
//
// A span watermark can be recorded before that partition has ever settled its
// counters, so the write needs a counter half to carry. It must be one that
// claims nothing — a fabricated range would skip offsets no counter ever saw.
var unsettled = State{
	Range:          Range{Min: 0, Max: nothingCounted},
	Flag:           After,
	WrittenThrough: NothingWritten,
}

// nothingCounted ends an empty counted range, so Resolve returns the first
// offset. Numerically NothingWritten, but a different fact about a different
// watermark, so it is named separately.
const nothingCounted int64 = -1

// Guard is the pipeline's view of that state: what each partition has already
// counted, how far its spans are written, and the two writes that bracket a
// counter insert.
//
// The cache is read back from the store on the first use and again whenever the
// consumer group reassigns partitions, because a partition that moves between
// workers is counted by whoever owns it and only that worker's writes are in
// its own cache.
type Guard struct {
	store Store

	// Invalidate is called from the rebalance callback while the pipeline
	// goroutine is inside its poll, so every field below is held under the lock.
	mu      sync.Mutex
	settled map[Partition]State
	stale   bool
}

// NewGuard returns a guard whose cache is empty and due a read. It does not
// touch the store: a store that is down at boot must show up as a red readiness
// probe, not as a crash loop.
func NewGuard(store Store) *Guard {
	return &Guard{store: store, settled: make(map[Partition]State), stale: true}
}

// Invalidate marks the cache due a re-read. The consumer group's assignment
// callback calls it: partitions can arrive from another worker, whose
// settlements this process never saw.
func (g *Guard) Invalidate() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.stale = true
}

// Refresh re-reads the state when it is due, and is a no-op otherwise. It must
// succeed before anything is counted — counting on a stale cache is what
// double-counts.
func (g *Guard) Refresh(ctx context.Context) error {
	g.mu.Lock()
	due := g.stale
	g.mu.Unlock()
	if !due {
		return nil
	}

	settled, err := g.store.Load(ctx)
	if err != nil {
		return err
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	// Whole-cache replacement is safe because nothing is cached until the store
	// has accepted it, so a read can only return what this worker wrote or
	// something newer from the worker that held the partition before it.
	g.settled, g.stale = settled, false
	return nil
}

// Counting resolves an incoming batch's per-partition offset ranges against
// what is already counted.
func (g *Guard) Counting(incoming map[Partition]Range) Decisions {
	g.mu.Lock()
	defer g.mu.Unlock()

	decisions := make(Decisions, len(incoming))
	for partition, offsets := range incoming {
		settled, known := g.settled[partition]
		if !known {
			decisions[partition] = Resolve(nil, offsets)
			continue
		}
		decisions[partition] = Resolve(&settled, offsets)
	}
	return decisions
}

// Advance turns the offsets a counter insert is about to make exact into the
// range to record for each partition.
//
// reached is how far the counters have been folded in per partition — every
// offset up to its Max has had all of its contributions drained into this block
// or an earlier one. A partition that has not moved past a confirmed settlement
// is left out; there is nothing new to record for it.
func (g *Guard) Advance(reached map[Partition]Range) Settlement {
	g.mu.Lock()
	defer g.mu.Unlock()

	settlement := make(Settlement, len(reached))
	for partition, offsets := range reached {
		settled, known := g.settled[partition]
		switch {
		case !known:
			settlement[partition] = offsets
		case settled.Flag == After && offsets.Max <= settled.Max:
			continue
		case settled.Flag == After:
			settlement[partition] = Range{
				Min: max(offsets.Min, settled.Max+1),
				Max: offsets.Max,
			}
		default:
			// The settlement before this one never confirmed, so its range was
			// counted again into this block: keep it inside what is confirmed
			// now. Max stays at what the counters have actually reached — a
			// range claimed exact past that would leave a hole rather than a
			// duplicate.
			settlement[partition] = Range{
				Min: min(offsets.Min, settled.Min),
				Max: offsets.Max,
			}
		}
	}
	return settlement
}

// Writing narrows the offsets whose spans the store has acknowledged to the
// partitions where that has actually moved, so a turn of the loop that stored
// nothing new writes nothing.
//
// reached is the prefix of held batches whose sampling window has closed: every
// span from an offset at or below its Max has left the buffer and been
// inserted. A partition already recorded at or past that offset is left out.
func (g *Guard) Writing(reached map[Partition]Range) Written {
	g.mu.Lock()
	defer g.mu.Unlock()

	advanced := make(Written, len(reached))
	for partition, offsets := range reached {
		if settled, known := g.settled[partition]; known && offsets.Max <= settled.WrittenThrough {
			continue
		}
		advanced[partition] = offsets.Max
	}
	return advanced
}

// Begin records a settlement as started, before the counters are inserted, and
// records the span watermark alongside it.
//
// The span watermark goes first. It is already safe to store by the time Begin
// runs — Settle inserts the expired spans before it computes what reached the
// store — and writing it ahead of the counter bracket means a settlement that
// fails half way still leaves the spans recognisable as stored, which is one
// less batch to duplicate on the replay.
func (g *Guard) Begin(ctx context.Context, settlement Settlement, written Written) error {
	if err := g.saveWritten(ctx, written); err != nil {
		return err
	}
	return g.save(ctx, settlement, Before)
}

// Complete records a settlement as stored, once the insert is acknowledged. The
// span watermark is not repeated here; Begin already stored it.
func (g *Guard) Complete(ctx context.Context, settlement Settlement) error {
	return g.save(ctx, settlement, After)
}

// save records the counter half and caches what the store accepted.
//
// The cached state keeps its span watermark: the two halves share one cache
// entry and only one of them is moving here, so replacing the whole entry would
// walk the other backwards to "nothing written" and duplicate a replay.
func (g *Guard) save(ctx context.Context, settlement Settlement, flag Flag) error {
	if len(settlement) == 0 {
		return nil
	}
	states := g.counterRows(settlement, flag)
	if err := g.store.Save(ctx, states); err != nil {
		return err
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	for partition, state := range states {
		g.settled[partition] = state
	}
	return nil
}

// counterRows builds each partition's row with the counter half replaced and
// the span watermark carried through from the cache.
func (g *Guard) counterRows(settlement Settlement, flag Flag) map[Partition]State {
	g.mu.Lock()
	defer g.mu.Unlock()

	rows := make(map[Partition]State, len(settlement))
	for partition, offsets := range settlement {
		row := unsettled
		if cached, seen := g.settled[partition]; seen {
			row = cached
		}
		row.Range, row.Flag = offsets, flag
		rows[partition] = row
	}
	return rows
}

// saveWritten records the span half and caches what the store accepted.
func (g *Guard) saveWritten(ctx context.Context, written Written) error {
	if len(written) == 0 {
		return nil
	}
	if err := g.store.SaveWritten(ctx, written); err != nil {
		return err
	}

	g.mu.Lock()
	defer g.mu.Unlock()
	for partition, offset := range written {
		row, seen := g.settled[partition]
		if !seen {
			row = unsettled
		}
		row.WrittenThrough = offset
		g.settled[partition] = row
	}
	return nil
}

// Settled reports one partition's cached state, which is what a log line and a
// test read.
func (g *Guard) Settled(partition Partition) (State, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	state, known := g.settled[partition]
	return state, known
}
