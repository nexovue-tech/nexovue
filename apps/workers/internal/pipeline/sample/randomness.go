package sample

import (
	"crypto/sha256"
	"encoding/binary"
	"strconv"
	"strings"
)

// A probabilistic decision is taken by comparing 56 bits of randomness that
// belong to the trace against a rejection threshold over the same range: keep
// when randomness >= threshold. That is the W3C Trace Context Level 2 and
// OpenTelemetry form, and it is what makes independent samplers agree — a
// collector sampling in front of us keeps a subset of what we keep, so the
// combined probability is still something you can reason about.
const (
	// randomnessValues is 2^56: the number of distinct randomness values.
	randomnessValues = uint64(1) << 56
	randomnessMask   = randomnessValues - 1

	// randomnessDigits is 56 bits written as hex, which is how both an r-value
	// and the tail of a trace id are encoded.
	randomnessDigits = 14

	// traceIDDigits is a W3C trace id: 128 bits, hex-encoded.
	traceIDDigits = 32

	hexBase = 16
)

// The tracestate member OpenTelemetry carries its sampling fields in, and the
// sub-key inside it holding an explicit randomness value.
const (
	otelTraceStateKey = "ot"
	randomnessSubKey  = "rv"
)

// traceRandomness is the 56 bits this trace is judged against.
//
// An explicit r-value wins over the trace id because an upstream sampler that
// published one has already decided against it, and agreeing with that
// decision is the entire point of using the trace's own randomness.
func traceRandomness(trace traceFacts) uint64 {
	if randomness, ok := randomnessFromTraceState(trace.traceState); ok {
		return randomness
	}
	if randomness, ok := randomnessFromTraceID(trace.traceID); ok {
		return randomness
	}
	return hashedRandomness(trace.traceID)
}

// rejectionThreshold is how many of the 2^56 randomness values this percentage
// drops. The extremes fall out of the arithmetic rather than being special
// cases: 0% rejects every value, 100% rejects none.
func rejectionThreshold(percent int) uint64 {
	switch {
	case percent <= 0:
		return randomnessValues
	case percent >= 100:
		return 0
	}
	return randomnessValues - uint64(percent)*randomnessValues/100
}

// randomnessFromTraceID reads the randomness the W3C spec requires to be in the
// right-most 7 bytes of a trace id, which is its last 14 hex digits. An id that
// is not a 128-bit hex id has none to read.
func randomnessFromTraceID(traceID string) (uint64, bool) {
	if len(traceID) != traceIDDigits {
		return 0, false
	}
	return parseRandomness(traceID[traceIDDigits-randomnessDigits:])
}

// randomnessFromTraceState reads `ot=rv:<14 hex digits>` out of a W3C
// tracestate: comma-separated members, each `key=value`, the OpenTelemetry
// member's value being semicolon-separated `name:value` fields.
func randomnessFromTraceState(traceState string) (uint64, bool) {
	for member := range strings.SplitSeq(traceState, ",") {
		key, value, separated := strings.Cut(strings.TrimSpace(member), "=")
		if !separated || key != otelTraceStateKey {
			continue
		}
		for field := range strings.SplitSeq(value, ";") {
			name, digits, separated := strings.Cut(field, ":")
			if separated && name == randomnessSubKey {
				return parseRandomness(digits)
			}
		}
	}
	return 0, false
}

// parseRandomness accepts exactly the 14 hex digits that encode 56 bits.
func parseRandomness(digits string) (uint64, bool) {
	if len(digits) != randomnessDigits {
		return 0, false
	}
	value, err := strconv.ParseUint(digits, hexBase, 64)
	if err != nil {
		return 0, false
	}
	return value, true
}

// hashedRandomness is the fallback for a trace id carrying no usable randomness
// — an older SDK, or an id that is not 128 bits. Hashing into the same 56-bit
// range keeps the decision fair and reproducible; it just cannot agree with
// another sampler, which an id without randomness never could anyway.
//
// SHA-256 rather than FNV-1a: a threshold comparison reads the high bits of the
// hash, and FNV-1a leaves those clustered for the short, structured ids this
// path exists to serve. Measured, not assumed — 200 sequential ids hashed with
// FNV-1a kept none at 10%. It runs once per trace, not once per span.
func hashedRandomness(traceID string) uint64 {
	digest := sha256.Sum256([]byte(traceID))
	return binary.BigEndian.Uint64(digest[:8]) & randomnessMask
}
