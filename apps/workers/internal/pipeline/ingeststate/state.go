// Package ingeststate makes trace assembly idempotent across a restart. One
// row per partition carries both watermarks: how far its counters are exact,
// and how far its span rows are in the store.
//
// The first design leaned on ClickHouse's own insert deduplication to recognise
// a replayed counter block. Measurement showed what that misses: the
// deduplication log does not survive a restart of the store, which is the very
// event that causes the replay. Keeper does survive it, so the record of what
// each partition has already counted lives in a KeeperMap table.
//
// The counter protocol is ClickHouse's own, from the design of their Kafka
// connector
// (https://github.com/ClickHouse/clickhouse-kafka-connect/blob/main/docs/DESIGN.md):
// a per-partition offset range is written BEFORE the insert and again AFTER the
// store acknowledges it, and an incoming batch is classified against that range
// as Same, Overlapping, Contains or New. One deliberate deviation from the
// connector is recorded on Resolve.
//
// The span watermark needs no such bracket, and that difference is the point.
// A counter re-applied is a wrong number; a span row re-applied is a duplicate
// row, so the watermark is a single offset recorded once the store has
// acknowledged the insert. Losing that write costs a duplicate on the next
// replay, which is what happened before it existed — never a hole.
package ingeststate

import (
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// Flag is how far a settlement got.
//
// BEFORE is written ahead of the counter insert and AFTER once the store has
// acknowledged it, so a recovered BEFORE says "the insert may or may not have
// landed" and a recovered AFTER says "it did".
type Flag string

const (
	// Before marks a settlement whose outcome is not yet known.
	Before Flag = "BEFORE"
	// After marks a settlement the store acknowledged.
	After Flag = "AFTER"
)

// ParseFlag turns a stored flag back into one of the two values, refusing
// anything else rather than guessing: an unrecognised flag would be resolved
// either into a double count or into a hole in the counters.
func ParseFlag(value string) (Flag, error) {
	switch Flag(value) {
	case Before:
		return Before, nil
	case After:
		return After, nil
	default:
		return "", fmt.Errorf("unknown ingest state %q, want %s or %s", value, Before, After)
	}
}

// Range is an inclusive span of Kafka offsets on one partition.
type Range struct {
	Min int64
	Max int64
}

// RangesOf bounds the offsets each partition contributes to a set of records.
//
// It answers both questions this package is asked: what an incoming batch
// covers, and how far the counters of the batches already held have been
// folded in.
func RangesOf(positions []consume.Position) map[Partition]Range {
	ranges := make(map[Partition]Range)
	for _, at := range positions {
		key := Partition{Topic: at.Topic, ID: at.Partition}
		offsets, seen := ranges[key]
		if !seen {
			ranges[key] = Range{Min: at.Offset, Max: at.Offset}
			continue
		}
		ranges[key] = Range{
			Min: min(offsets.Min, at.Offset),
			Max: max(offsets.Max, at.Offset),
		}
	}
	return ranges
}

// NothingWritten is the span watermark of a partition none of whose spans have
// reached the store. Kafka offsets start at zero, so the value below all of
// them is minus one; zero would claim offset zero was written and skip it.
const NothingWritten int64 = -1

// State is one partition's durable record: the offsets of the most recent
// counter settlement, how far that settlement got, and the offset whose spans
// are certainly in the store.
//
// The two watermarks move at different speeds and are read by different
// stages. WrittenThrough advances one sampling window after a batch arrives;
// the counter range advances a minute bucket and its late grace after that, so
// WrittenThrough normally runs well ahead of Max.
type State struct {
	Range
	Flag Flag

	// WrittenThrough is the highest offset whose span rows the store has
	// acknowledged. NothingWritten when none have.
	WrittenThrough int64
}

// storeFrom is the lowest offset whose spans still have to be written.
func (s State) storeFrom() int64 { return s.WrittenThrough + 1 }

// Partition names one partition of one topic, and is the KeeperMap primary key.
type Partition struct {
	Topic string
	ID    int32
}

const partitionKeySeparator = ":"

// Key renders the primary key stored in ClickHouse.
func (p Partition) Key() string {
	return p.Topic + partitionKeySeparator + strconv.FormatInt(int64(p.ID), 10)
}

// ParsePartitionKey reverses Key. It splits on the last separator so a topic
// containing one is still read back correctly.
func ParsePartitionKey(key string) (Partition, error) {
	at := strings.LastIndex(key, partitionKeySeparator)
	if at <= 0 || at == len(key)-1 {
		return Partition{}, fmt.Errorf("malformed partition key %q, want topic%spartition",
			key, partitionKeySeparator)
	}
	id, err := strconv.ParseInt(key[at+1:], 10, 32)
	if err != nil {
		return Partition{}, fmt.Errorf("malformed partition key %q: %w", key, err)
	}
	return Partition{Topic: key[:at], ID: int32(id)}, nil
}

// Scenario is how an incoming batch overlaps the last settled range. The four
// names are ClickHouse's, from the connector's design.
type Scenario string

const (
	// New — the batch begins past the settled range. The common case.
	New Scenario = "new"
	// Same — the batch is exactly the settled range.
	Same Scenario = "same"
	// Overlapping — the batch reaches past the settled range.
	Overlapping Scenario = "overlapping"
	// Contains — the batch falls entirely inside the settled range.
	Contains Scenario = "contains"
)

// Classify names how an incoming batch overlaps a settled range.
//
// Any part of the batch below the settled range is discarded first, as the
// connector does: offsets under it belong to an earlier settlement, and a new
// settlement is only ever recorded once the one before it was confirmed.
func Classify(settled, incoming Range) Scenario {
	if incoming.Min > settled.Max {
		return New
	}
	incoming.Min = max(incoming.Min, settled.Min)
	switch {
	case incoming.Min == settled.Min && incoming.Max == settled.Max:
		return Same
	case incoming.Max > settled.Max:
		return Overlapping
	default:
		return Contains
	}
}

// firstOffset counts everything. No stored state means nothing has been
// settled, so no offset can already have been counted.
const firstOffset = math.MinInt64

// Decision is what to do with an incoming batch: count every offset at or above
// CountFrom, write the spans of every offset at or above StoreFrom, and do
// neither below them.
//
// The two thresholds are independent because the two watermarks are. A replay
// normally lands between them — its counters are already exact while its spans
// are already stored — and that band is most of what a restart re-reads.
type Decision struct {
	Scenario Scenario
	// CountFrom is the lowest offset that still has to reach a counter.
	CountFrom int64
	// StoreFrom is the lowest offset whose spans still have to be written.
	StoreFrom int64
	// Ambiguous marks the one case a duplicate counter is still possible: the
	// settled range was left at BEFORE, so it is counted again and ClickHouse's
	// insert deduplication is what drops the copy when the store stayed up. It
	// says nothing about StoreFrom, which is never written before its insert.
	Ambiguous bool
}

// Counts reports whether this offset still has to be counted.
func (d Decision) Counts(offset int64) bool { return offset >= d.CountFrom }

// Stores reports whether this offset's spans still have to be written.
func (d Decision) Stores(offset int64) bool { return offset >= d.StoreFrom }

// Resolve decides which offsets of an incoming batch still have to be counted.
//
// It is the connector's table reduced to its one number. AFTER means the
// settled range reached storage, so counting resumes past it. BEFORE means the
// insert may or may not have landed, and the connector's answer — reinsert, and
// let the store's own deduplication drop the copy — is what recounting from the
// settled minimum does here.
//
// Deviation: the connector routes Contains over BEFORE to a dead-letter queue,
// because it cannot reconstruct the batch the missing chunk belonged to. Here a
// batch is only a window onto a stream of spans, so the same case is counted
// like the others. Parking well-formed spans would put a hole in the counters,
// and an undercount is a worse answer than a bounded overcount for a monitoring
// product.
func Resolve(settled *State, incoming Range) Decision {
	if settled == nil {
		return Decision{Scenario: New, CountFrom: firstOffset, StoreFrom: firstOffset}
	}
	decision := Decision{Scenario: Classify(settled.Range, incoming), StoreFrom: settled.storeFrom()}
	if decision.Scenario == New || settled.Flag == After {
		decision.CountFrom = settled.Max + 1
		return decision
	}
	decision.CountFrom, decision.Ambiguous = settled.Min, true
	return decision
}

// Decisions is one Decision per partition of an incoming batch.
type Decisions map[Partition]Decision

// Counts reports whether this offset still has to be counted. A partition with
// no decision is counted: an unrecognised partition can only be one nothing is
// known about, and counting it errs towards a duplicate rather than a hole.
func (d Decisions) Counts(partition Partition, offset int64) bool {
	decision, known := d[partition]
	return !known || decision.Counts(offset)
}

// Stores reports whether this offset's spans still have to be written. A
// partition with no decision is written, for the same reason it is counted: an
// unrecognised partition is one nothing is known about, and a duplicate row is
// recoverable where a missing span is not.
func (d Decisions) Stores(partition Partition, offset int64) bool {
	decision, known := d[partition]
	return !known || decision.Stores(offset)
}

// Settlement is the offsets one counter insert makes exact, per partition.
type Settlement map[Partition]Range

// Written is the highest offset per partition whose spans the store has
// acknowledged, for the partitions where that has moved.
type Written map[Partition]int64
