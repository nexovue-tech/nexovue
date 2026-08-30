// Package insertdedup derives the deduplication token an insert has to carry
// when ClickHouse will not hash the block for us.
//
// The store deduplicates a repeated insert by hashing the block it receives —
// which is what `non_replicated_deduplication_window` on the summing rollups
// (000016, 000021) was added for, and what makes a retry after a lost
// acknowledgement harmless. But it only covers an insert that *carries* its
// data. A rollup's `AggregateFunction` state has to be folded by the server, so
// those writers send `INSERT … SELECT … FROM input(…)` instead, and that form
// is not covered: measured here on 26.3.20.7, the same statement run twice
// summed in twice, while the same rows sent as plain VALUES deduplicated to
// one. Naming `insert_deduplication_token` restores the guarantee — with the
// token, the same statement twice deduplicated to one row, across three
// partitions in one insert as well.
//
// The token is derived from the rows themselves, so a minute re-derived from
// the same offsets produces the same token and is recognised as the repeat it
// is. That is the semantics block hashing was meant to give.
package insertdedup

import (
	"encoding/hex"
	"fmt"
	"hash"
	"hash/fnv"
)

// Setting is ClickHouse's name for the token, spelled once.
const Setting = "insert_deduplication_token"

// Field and row separators. Without them "ab","c" and "a","bc" would hash the
// same, and two rows could be repartitioned into one without changing the token.
const (
	fieldSeparator = "\x1f"
	rowSeparator   = "\x1e"
)

// Digest accumulates an insert's content. 128-bit because a collision is an
// insert silently dropped as a duplicate, which is lost data rather than a
// retry.
type Digest struct{ hash hash.Hash }

// NewDigest starts a token derivation. Rows are folded in, in the order they
// are written, so the same minute re-derived from the same offsets is
// recognised as the repeat it is.
func NewDigest() *Digest { return &Digest{hash: fnv.New128a()} }

// AddRow folds one row into the token. The caller passes the fields in the
// order the block carries them; a drain that is already sorted therefore hashes
// the same every time it is re-derived.
func (d *Digest) AddRow(fields ...any) {
	for _, field := range fields {
		// hash.Hash documents that Write never returns an error.
		_, _ = fmt.Fprintf(d.hash, "%v%s", field, fieldSeparator)
	}
	_, _ = d.hash.Write([]byte(rowSeparator))
}

// Token is the value to send as `insert_deduplication_token`.
func (d *Digest) Token() string { return hex.EncodeToString(d.hash.Sum(nil)) }
