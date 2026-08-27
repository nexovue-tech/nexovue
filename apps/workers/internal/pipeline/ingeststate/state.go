// Package ingeststate makes the exact per-minute counters idempotent across a
// ClickHouse restart.
//
// ADR 0060 leaned on ClickHouse's own insert deduplication to recognise a
// replayed counter block. ADR 0061 measured what that misses: the deduplication
// log does not survive a restart of the store, which is the very event that
// causes the replay. Keeper does survive it, so the record of what each
// partition has already counted lives in a KeeperMap table.
//
// The protocol is ClickHouse's own, from the design of their Kafka connector
// (https://github.com/ClickHouse/clickhouse-kafka-connect/blob/main/docs/DESIGN.md):
// a per-partition offset range is written BEFORE the insert and again AFTER the
// store acknowledges it, and an incoming batch is classified against that range
// as Same, Overlapping, Contains or New. One deliberate deviation from the
// connector is recorded on Resolve.
package ingeststate

import (
	"fmt"
	"math"
	"strconv"
	"strings"
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

// State is one partition's durable record: the offsets of the most recent
// settlement and how far that settlement got.
type State struct {
	Range
	Flag Flag
}

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
// CountFrom, and none below it.
type Decision struct {
	Scenario Scenario
	// CountFrom is the lowest offset that still has to reach a counter.
	CountFrom int64
	// Ambiguous marks the one case a duplicate is still possible: the settled
	// range was left at BEFORE, so it is counted again and ADR 0060's insert
	// deduplication is what drops the copy when the store stayed up.
	Ambiguous bool
}

// Counts reports whether this offset still has to be counted.
func (d Decision) Counts(offset int64) bool { return offset >= d.CountFrom }

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
		return Decision{Scenario: New, CountFrom: firstOffset}
	}
	scenario := Classify(settled.Range, incoming)
	if scenario == New || settled.Flag == After {
		return Decision{Scenario: scenario, CountFrom: settled.Max + 1}
	}
	return Decision{Scenario: scenario, CountFrom: settled.Min, Ambiguous: true}
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

// Settlement is the offsets one counter insert makes exact, per partition.
type Settlement map[Partition]Range

// states renders a settlement as the rows to store under one flag.
func (s Settlement) states(flag Flag) map[Partition]State {
	rows := make(map[Partition]State, len(s))
	for partition, offsets := range s {
		rows[partition] = State{Range: offsets, Flag: flag}
	}
	return rows
}
