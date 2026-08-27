package ingeststate

import (
	"context"
	"sync"
)

// Store is the durable home of the per-partition state. KeeperMap is the
// implementation; the interface is what lets the state machine be tested
// without a cluster.
type Store interface {
	Load(ctx context.Context) (map[Partition]State, error)
	Save(ctx context.Context, states map[Partition]State) error
}

// Guard is the pipeline's view of that state: what each partition has already
// counted, and the two writes that bracket a counter insert.
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

// Begin records a settlement as started, before the counters are inserted.
func (g *Guard) Begin(ctx context.Context, settlement Settlement) error {
	return g.save(ctx, settlement, Before)
}

// Complete records a settlement as stored, once the insert is acknowledged.
func (g *Guard) Complete(ctx context.Context, settlement Settlement) error {
	return g.save(ctx, settlement, After)
}

func (g *Guard) save(ctx context.Context, settlement Settlement, flag Flag) error {
	if len(settlement) == 0 {
		return nil
	}
	states := settlement.states(flag)
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

// Settled reports one partition's cached state, which is what a log line and a
// test read.
func (g *Guard) Settled(partition Partition) (State, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	state, known := g.settled[partition]
	return state, known
}
