package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
)

// Every probe record carries its own identity in a column the store already
// has, so a duplicate is an exact repeat and a gap is a missing value — no
// probe table, and no join against what the producer remembers.
//
// Spans use the ids themselves: `trace_id` is run + partition + trace sequence,
// `span_id` is trace sequence + span index. Logs use the body, which is stored
// verbatim. Both encode the Kafka partition, so duplication and loss can be
// read per partition and lined up against which partition moved.
//
// The encodings are hexadecimal on purpose. A masking rule that saw a long run
// of decimal digits would read it as a payment card and rewrite the identity
// this measurement rests on.
const (
	runIDBytes = 4

	// Column widths the store fixes: trace_id is FixedString(32), span_id is
	// FixedString(16), both hex.
	traceIDHexLen = 32
	spanIDHexLen  = 16
)

// runID names one probe run. Every query in the report filters on it, so two
// runs against the same stack never mix.
func newRunID() (string, error) {
	raw := make([]byte, runIDBytes)
	if _, err := rand.Read(raw); err != nil {
		return "", fmt.Errorf("generating run id: %w", err)
	}
	return hex.EncodeToString(raw), nil
}

// traceID is run(8) + partition(4) + sequence(8) + padding(12).
func traceID(run string, partition int32, sequence uint32) string {
	id := fmt.Sprintf("%s%04x%08x%012x", run, uint16(partition), sequence, 0) //nolint:gosec // partition count is small
	return id[:traceIDHexLen]
}

// spanID is trace sequence(8) + span index(8), unique within one run.
func spanID(sequence uint32, index int) string {
	id := fmt.Sprintf("%08x%08x", sequence, uint32(index)) //nolint:gosec // index is a small loop counter
	return id[:spanIDHexLen]
}

// logBody carries the same three facts spans put in their ids. `logs.body` is
// stored as sent, so this string is the identity the count reads back.
func logBody(run string, partition int32, sequence uint32) string {
	return fmt.Sprintf("nvprobe %s p%04x n%08x", run, uint16(partition), sequence) //nolint:gosec // partition count is small
}

// spanService and logService isolate a run's rows from every other tenant's, so
// the counting query never has to reason about background traffic.
func spanService(run string) string { return "nvprobe-spans-" + run }

func logService(run string) string { return "nvprobe-logs-" + run }

// peerService is the callee every probe edge points at. Nothing reports under
// it, so the store holds it parenthesised — the marker the edge accumulator
// puts on a name it was told rather than one that reported for itself.
func peerService(run string) string { return "nvprobe-peer-" + run }
