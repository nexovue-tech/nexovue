package resend

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"
)

type fakeClaims struct {
	owners  map[string]Owner
	claimed []Claim
	err     error
}

func (f *fakeClaims) Owners(_ context.Context, digests []string) (map[string]Owner, error) {
	if f.err != nil {
		return nil, f.err
	}
	found := map[string]Owner{}
	for _, digest := range digests {
		if owner, ok := f.owners[digest]; ok {
			found[digest] = owner
		}
	}
	return found, nil
}

func (f *fakeClaims) Claim(_ context.Context, claims []Claim) error {
	if f.err != nil {
		return f.err
	}
	f.claimed = append(f.claimed, claims...)
	return nil
}

// hexOf stands a short name in for a 64-hex digest, the only shape kept.
func hexOf(name string) string {
	if name == "" {
		return ""
	}
	return strings.Repeat(fmt.Sprintf("%x", name[len(name)-1]), 32)
}

func record(digest string, partition int32, offset int64) *kgo.Record {
	r := &kgo.Record{Topic: "spans-raw", Partition: partition, Offset: offset}
	if digest != "" {
		r.Headers = []kgo.RecordHeader{{Key: DigestHeader, Value: []byte(hexOf(digest))}}
	}
	return r
}

// Group 9, C2: a sender resends an export the receiver gave up on, and the copy
// arrives at a new offset. Skipped, acknowledged, stored once.
func TestARecordWhoseContentAnotherOffsetClaimedIsSkipped(t *testing.T) {
	claims := &fakeClaims{owners: map[string]Owner{hexOf("d1"): {Topic: "spans-raw", Partition: 0, Offset: 5}}}
	skip := New(claims, slog.New(slog.DiscardHandler)).Skip(context.Background(), []*kgo.Record{record("d1", 0, 9), record("d2", 0, 10)})

	if !skip[0] || skip[1] {
		t.Fatalf("skip = %v, want the resend skipped and the new record kept", skip)
	}
	if len(claims.claimed) != 1 || claims.claimed[0].Digest != hexOf("d2") {
		t.Fatalf("claimed %+v, want the kept record's claim only", claims.claimed)
	}
}

// A replay of the very offset that claimed the content — after a crash, before
// the commit — is the original, and must be processed.
func TestTheOffsetThatClaimedTheContentIsNotItsOwnResend(t *testing.T) {
	claims := &fakeClaims{owners: map[string]Owner{hexOf("d1"): {Topic: "spans-raw", Partition: 0, Offset: 5}}}

	if skip := New(claims, slog.New(slog.DiscardHandler)).Skip(context.Background(), []*kgo.Record{record("d1", 0, 5)}); skip[0] {
		t.Fatal("a replay of the claiming offset was skipped; that loses the record")
	}
}

func TestTwoCopiesInOneBatchKeepTheFirst(t *testing.T) {
	claims := &fakeClaims{owners: map[string]Owner{}}
	skip := New(claims, slog.New(slog.DiscardHandler)).Skip(context.Background(), []*kgo.Record{record("d1", 0, 1), record("d1", 0, 2)})

	if skip[0] || !skip[1] {
		t.Fatalf("skip = %v, want the first kept and the second skipped", skip)
	}
}

func TestARecordWithoutADigestIsAlwaysKept(t *testing.T) {
	claims := &fakeClaims{owners: map[string]Owner{}}

	if skip := New(claims, slog.New(slog.DiscardHandler)).Skip(context.Background(), []*kgo.Record{record("", 0, 1)}); skip[0] {
		t.Fatal("a record from a receiver that stamps no digest was skipped")
	}
}

// A duplicate is better than a loss: when the claims cannot be read, everything
// is kept.
func TestAnUnreadableClaimStoreKeepsEverything(t *testing.T) {
	claims := &fakeClaims{err: errors.New("clickhouse down")}

	if skip := New(claims, slog.New(slog.DiscardHandler)).Skip(context.Background(), []*kgo.Record{record("d1", 0, 1)}); skip[0] {
		t.Fatal("a failure to check skipped a record")
	}
}
