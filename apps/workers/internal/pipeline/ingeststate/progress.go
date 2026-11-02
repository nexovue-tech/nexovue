package ingeststate

import (
	"cmp"
	"context"
	"fmt"
	"maps"
	"slices"
	"sync"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// progressTable holds one row per partition: how far its spans are in every
// derived rollup, and when that record was received (migration 000037). The
// scheduler reads it to decide which minute is settled enough to judge.
const progressTable = "span_derived_progress"

// MaxProgressHold bounds how long a refused derived insert may hold progress
// back (design 2.1, item 1). A missing minute can change a verdict only while it
// sits inside a window, at most the 10-minute sustain plus closing, so 15 minutes
// covers it. Held for ever, one row the store keeps refusing would switch
// detection off for good.
const MaxProgressHold = 15 * time.Minute

// Mark is how far one partition is counted into the derived rollups.
type Mark struct {
	// CountedThrough is the highest offset whose spans are in every rollup.
	CountedThrough int64
	// ReceivedAt is when the receiver took the newest record counted with it.
	ReceivedAt time.Time
}

// Marks is one Mark per partition.
type Marks map[Partition]Mark

// ProgressStore is the durable home of the marks. KeeperMap is the
// implementation; the interface is what lets the hold be tested without a
// cluster.
type ProgressStore interface {
	// Load reads how far every partition is recorded as counted.
	Load(ctx context.Context) (Written, error)

	// Save records the marks, overwriting whatever each partition had.
	Save(ctx context.Context, marks Marks) error
}

// Progress records how far each partition is counted, after the settlement's
// derived inserts were acknowledged.
//
// A derived insert the store refused holds every partition the caller names:
// its rows were stepped over (traceassembly/refusal.go), so the minutes they
// belonged to have a hole, and progress past them would let detection judge that
// hole as a drop in traffic (C22). A named partition is held whether or not this
// settlement carries a mark for it: the refused rows can come from batches that
// reach the settled prefix only later. A hold ends MaxProgressHold after it
// began, refused or not since, and the partition's newest mark is then written.
//
// It is not safe for concurrent use, except Invalidate, which the rebalance
// callback calls while the pipeline goroutine is inside its poll.
type Progress struct {
	store   ProgressStore
	maxHold time.Duration

	mu sync.Mutex
	// recorded is what the store holds, so a replay never writes a mark
	// behind it.
	recorded Written
	stale    bool
	held     map[Partition]hold
}

// hold is one partition's progress held back since a refusal, and the newest
// mark it reached in the meantime, if it reached any.
type hold struct {
	since  time.Time
	newest Mark
	marked bool
}

// reach folds a settled mark into the hold.
func (h hold) reach(mark Mark) hold {
	if !h.marked {
		return hold{since: h.since, newest: mark, marked: true}
	}
	return hold{since: h.since, newest: newer(h.newest, mark), marked: true}
}

// Recorded is what one call wrote and held.
type Recorded struct {
	Written int
	Held    int
	// Released are the partitions whose hold reached MaxProgressHold and were
	// advanced anyway, past a refusal.
	Released []Partition
}

// NewProgress returns progress whose cache is due a read. It does not touch the
// store: a store that is down at boot must show up as a red readiness probe.
func NewProgress(store ProgressStore, maxHold time.Duration) *Progress {
	return &Progress{
		store:    store,
		maxHold:  maxHold,
		recorded: make(Written),
		stale:    true,
		held:     make(map[Partition]hold),
	}
}

// Invalidate marks the cache due a re-read. A partition can arrive from another
// worker, whose progress this process never saw.
func (p *Progress) Invalidate() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.stale = true
}

// Refresh re-reads the recorded marks when they are due, and is a no-op
// otherwise.
func (p *Progress) Refresh(ctx context.Context) error {
	p.mu.Lock()
	due := p.stale
	p.mu.Unlock()
	if !due {
		return nil
	}

	recorded, err := p.store.Load(ctx)
	if err != nil {
		return err
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	p.recorded, p.stale = recorded, false
	return nil
}

// Forget drops the holds of partitions that left for another worker, so a hold
// ending later cannot write for a partition this process no longer owns (C1).
// Memory only, because the rebalance callback calls it.
func (p *Progress) Forget(partitions []Partition) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, partition := range partitions {
		delete(p.held, partition)
	}
}

// Record writes the marks of one settlement whose derived inserts are
// acknowledged, and ends any hold that has lasted MaxProgressHold. It must be
// called on every settle, with no marks when nothing moved, so a hold on a
// partition that has gone quiet still ends.
//
// refused names the partitions to hold because the store refused derived rows
// of this settlement that may carry their records; nil when it refused none.
// Nothing changes in memory unless the write succeeds, so the call can be
// retried.
func (p *Progress) Record(
	ctx context.Context,
	now time.Time,
	marks Marks,
	refused []Partition,
) (Recorded, error) {
	due, held, released := p.plan(now, marks, refused)
	if err := p.store.Save(ctx, due); err != nil {
		return Recorded{}, err
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	p.held = held
	for partition, mark := range due {
		p.recorded[partition] = mark.CountedThrough
	}
	return Recorded{Written: len(due), Held: len(held), Released: released}, nil
}

// plan works out what to write and what stays held, without changing anything.
func (p *Progress) plan(
	now time.Time,
	marks Marks,
	refused []Partition,
) (Marks, map[Partition]hold, []Partition) {
	p.mu.Lock()
	defer p.mu.Unlock()

	held := maps.Clone(p.held)
	for _, partition := range refused {
		if _, holding := held[partition]; !holding {
			held[partition] = hold{since: now}
		}
	}
	due := make(Marks, len(marks))
	for partition, mark := range marks {
		if current, holding := held[partition]; holding {
			held[partition] = current.reach(mark)
			continue
		}
		due[partition] = mark
	}

	var released []Partition
	for partition, current := range held {
		if now.Sub(current.since) < p.maxHold {
			continue
		}
		if current.marked {
			due[partition] = current.newest
		}
		delete(held, partition)
		released = append(released, partition)
	}
	slices.SortFunc(released, func(a, b Partition) int {
		return cmp.Or(cmp.Compare(a.Topic, b.Topic), cmp.Compare(a.ID, b.ID))
	})

	// Never behind what the store holds: a replayed or stale settlement would
	// otherwise move a partition backwards and unsettle minutes already judged.
	for partition, mark := range due {
		if through, known := p.recorded[partition]; known && mark.CountedThrough <= through {
			delete(due, partition)
		}
	}
	return due, held, released
}

// newer is the mark further along.
func newer(current, candidate Mark) Mark {
	if candidate.CountedThrough > current.CountedThrough {
		return candidate
	}
	return current
}

// NewProgressStore returns the KeeperMap store over an existing pool. The
// caller owns the pool.
func NewProgressStore(pool *chpool.Pool) ProgressStore {
	return keeperProgress{pool: pool}
}

type keeperProgress struct{ pool *chpool.Pool }

// Load reads every partition's counted-through offset. The received-at is not
// read back: only the scheduler needs it.
func (k keeperProgress) Load(ctx context.Context) (Written, error) {
	var (
		key     proto.ColStr
		through proto.ColInt64
	)
	found := make(Written)
	query := ch.Query{
		Body: "SELECT " + partitionKeyColumn + ", counted_through FROM " + progressTable,
		Result: proto.Results{
			{Name: partitionKeyColumn, Data: &key},
			{Name: "counted_through", Data: &through},
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
	if err := k.pool.Do(ctx, query); err != nil {
		return nil, fmt.Errorf("read %s: %w", progressTable, err)
	}
	return found, nil
}

// Save records the marks, one insert for every partition that moved.
func (k keeperProgress) Save(ctx context.Context, marks Marks) error {
	if len(marks) == 0 {
		return nil
	}

	var (
		key        proto.ColStr
		through    proto.ColInt64
		receivedAt proto.ColDateTime64
	)
	receivedAt.WithPrecision(proto.PrecisionNano)
	for partition, mark := range marks {
		key.Append(partition.Key())
		through.Append(mark.CountedThrough)
		receivedAt.Append(mark.ReceivedAt)
	}

	input := proto.Input{
		{Name: partitionKeyColumn, Data: &key},
		{Name: "counted_through", Data: &through},
		{Name: "counted_through_received_at", Data: &receivedAt},
	}
	query := ch.Query{
		Body:     input.Into(progressTable),
		Input:    input,
		Settings: []ch.Setting{{Key: strictModeSetting, Value: "0"}},
	}
	if err := k.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("write %d rows to %s: %w", len(marks), progressTable, err)
	}
	return nil
}
