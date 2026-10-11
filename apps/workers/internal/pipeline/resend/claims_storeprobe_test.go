//go:build storeprobe

package resend

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
)

// Against a real server, because the first version passed every unit test and
// failed every lookup: ch-go writes a []string parameter unquoted, and the
// server cannot parse it as Array(String). Run with PROBE_CH_ADDR set.
func TestAClaimIsReadBackByItsDigest(t *testing.T) {
	pool, err := chpool.Dial(context.Background(), chpool.Options{ClientOptions: ch.Options{
		Address:  os.Getenv("PROBE_CH_ADDR"),
		User:     os.Getenv("PROBE_CH_USER"),
		Password: os.Getenv("PROBE_CH_PASSWORD"),
		Database: "nexovue",
	}})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer pool.Close()
	sum := sha256.Sum256([]byte(fmt.Sprintf("probe-%d", time.Now().UnixNano())))
	digest := hex.EncodeToString(sum[:])
	store := NewClickHouse(pool)
	owner := Owner{Topic: "spans-raw", Partition: 3, Offset: 41}

	if err := store.Claim(context.Background(), []Claim{{Digest: digest, Owner: owner}}); err != nil {
		t.Fatalf("claim: %v", err)
	}
	owners, err := store.Owners(context.Background(), []string{digest})
	if err != nil {
		t.Fatalf("owners: %v", err)
	}
	if owners[digest] != owner {
		t.Fatalf("owners = %v, want %v", owners, owner)
	}
}

// A batch of thousands of records, as the dev stack's own traffic made: the
// first version wrote them into one statement and passed max_query_size.
func TestAThousandsStrongBatchIsLookedUpWithinTheQueryLimit(t *testing.T) {
	pool, err := chpool.Dial(context.Background(), chpool.Options{ClientOptions: ch.Options{
		Address:  os.Getenv("PROBE_CH_ADDR"),
		User:     os.Getenv("PROBE_CH_USER"),
		Password: os.Getenv("PROBE_CH_PASSWORD"),
		Database: "nexovue",
	}})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer pool.Close()
	digests := make([]string, 5000)
	for i := range digests {
		sum := sha256.Sum256([]byte(fmt.Sprintf("bulk-%d-%d", time.Now().UnixNano(), i)))
		digests[i] = hex.EncodeToString(sum[:])
	}
	if _, err := NewClickHouse(pool).Owners(context.Background(), digests); err != nil {
		t.Fatalf("owners: %v", err)
	}
}
