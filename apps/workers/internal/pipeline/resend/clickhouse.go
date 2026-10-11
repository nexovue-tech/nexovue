package resend

import (
	"context"
	"fmt"
	"strings"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// claimsTable holds each digest's claiming record for fifteen minutes
// (ClickHouse migration 000033) — far longer than any sender waits to resend.
const claimsTable = "stored_payload_digests"

// ClickHouse keeps the claims in ClickHouse, where every worker and meter sees
// the same ones.
type ClickHouse struct{ pool *chpool.Pool }

// NewClickHouse returns a claim store over an existing pool.
func NewClickHouse(pool *chpool.Pool) *ClickHouse { return &ClickHouse{pool: pool} }

// lookupChunk bounds one lookup's statement: a thousand digests written out is
// about 67 KB, well inside ClickHouse's 256 KB max_query_size. A batch of five
// thousand in one statement exceeded it, on the dev stack's own traffic.
const lookupChunk = 1000

// Owners returns the claiming record of every digest asked for that has one.
func (c *ClickHouse) Owners(ctx context.Context, digests []string) (map[string]Owner, error) {
	owners := make(map[string]Owner, len(digests))
	for start := 0; start < len(digests); start += lookupChunk {
		if err := c.ownersOf(ctx, digests[start:min(start+lookupChunk, len(digests))], owners); err != nil {
			return nil, err
		}
	}
	return owners, nil
}

func (c *ClickHouse) ownersOf(ctx context.Context, digests []string, owners map[string]Owner) error {
	var (
		digest    proto.ColStr
		partition proto.ColInt32
		offset    proto.ColInt64
	)
	// The column is LowCardinality(String), and ch-go decodes only into its own type.
	topic := new(proto.ColStr).LowCardinality()
	err := c.pool.Do(ctx, ch.Query{
		Body: "SELECT digest, topic, partition, offset FROM " + claimsTable +
			" WHERE digest IN (" + quotedList(digests) + ")" +
			" ORDER BY claimed_at, partition, offset LIMIT 1 BY digest",
		Result: proto.Results{
			{Name: "digest", Data: &digest},
			{Name: "topic", Data: topic},
			{Name: "partition", Data: &partition},
			{Name: "offset", Data: &offset},
		},
		OnResult: func(context.Context, proto.Block) error {
			for i := range digest.Rows() {
				owners[digest.Row(i)] = Owner{
					Topic: topic.Row(i), Partition: partition.Row(i), Offset: offset.Row(i),
				}
			}
			return nil
		},
	})
	if err != nil {
		return fmt.Errorf("reading payload claims: %w", err)
	}
	return nil
}

// Claim records each digest's claiming record.
func (c *ClickHouse) Claim(ctx context.Context, claims []Claim) error {
	var (
		digest    proto.ColStr
		topic     proto.ColStr
		partition proto.ColInt32
		offset    proto.ColInt64
	)
	for _, claim := range claims {
		digest.Append(claim.Digest)
		topic.Append(claim.Owner.Topic)
		partition.Append(claim.Owner.Partition)
		offset.Append(claim.Owner.Offset)
	}
	err := c.pool.Do(ctx, ch.Query{
		Body: "INSERT INTO " + claimsTable + " (digest, topic, partition, offset) VALUES",
		Input: proto.Input{
			{Name: "digest", Data: &digest},
			{Name: "topic", Data: &topic},
			{Name: "partition", Data: &partition},
			{Name: "offset", Data: &offset},
		},
	})
	if err != nil {
		return fmt.Errorf("writing payload claims: %w", err)
	}
	return nil
}

// quotedList writes the digests as literals. Not a query parameter: ch-go
// sends a []string unquoted and mangles one passed as text, and the server
// refuses both (the store probe records it). Safe because every digest has been
// checked to be 64 hex characters (digestOf) — nothing in one can close a quote.
func quotedList(digests []string) string {
	quoted := make([]string, 0, len(digests))
	for _, digest := range digests {
		if isDigest([]byte(digest)) {
			quoted = append(quoted, "'"+digest+"'")
		}
	}
	if len(quoted) == 0 {
		return "''"
	}
	return strings.Join(quoted, ",")
}
