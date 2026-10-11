// Package resend skips a record that is a sender's resend of one already
// consumed (group 9, C2).
//
// When the receiver gives up on an export after its own retries, the sender is
// told 503 and sends the whole export again — but records of the first attempt
// may already be on the broker, so the copy lands at new offsets and would be
// stored, counted and billed twice. Nothing offset-based can see it.
//
// The receiver stamps each record with a digest of its tenant, project, signal
// and payload (DigestHeader). The first offset to carry a digest **claims** it,
// in ClickHouse, when the record is consumed — not when it is stored, because
// a trace waits in the sampling buffer for a minute and a resend arrives within
// seconds. A later record carrying a claimed digest at a different offset is a
// resend: acknowledged and skipped. A replay of the claiming offset itself, after
// a crash before the commit, is the original and is processed.
//
// The usage meters consume the same records under their own groups and reach
// the same verdict from the same claims, so a resend is not billed either.
//
// Fails open: a duplicate is better than a loss.
package resend

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"

	"github.com/ClickHouse/ch-go/chpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// DigestHeader carries the digest. The receiver writes it under the same name
// (apps/receiver/internal/ingest).
const DigestHeader = "nexovue-payload-digest"

// Owner is the record that claimed a digest.
type Owner struct {
	Topic     string
	Partition int32
	Offset    int64
}

// Claim is a digest and the record claiming it.
type Claim struct {
	Digest string
	Owner  Owner
}

// Claims is where digests are claimed and looked up.
type Claims interface {
	Owners(ctx context.Context, digests []string) (map[string]Owner, error)
	Claim(ctx context.Context, claims []Claim) error
}

// Skipped counts records skipped as resends; a monitor reads a burst of them as
// a receiver struggling to publish.
var Skipped = prometheus.NewCounter(prometheus.CounterOpts{
	Name: "nexovue_worker_resends_skipped_total",
	Help: "Records skipped because the same content was already consumed at another offset.",
})

// Skipper decides which records of a batch are resends.
type Skipper struct {
	claims Claims
	log    *slog.Logger
}

// New returns a skipper over a claim store, warning through log.
func New(claims Claims, log *slog.Logger) *Skipper { return &Skipper{claims: claims, log: log} }

// Skip reports, per record, whether it is a resend, and claims the rest.
func (s *Skipper) Skip(ctx context.Context, records []*kgo.Record) []bool {
	skip := make([]bool, len(records))
	digests := make([]string, len(records))
	var asked []string
	for i, record := range records {
		digests[i] = digestOf(record)
		if digests[i] != "" {
			asked = append(asked, digests[i])
		}
	}
	if len(asked) == 0 {
		return skip
	}

	owners, err := s.claims.Owners(ctx, asked)
	if err != nil {
		// Fails open, never quietly: every resend in this batch is now stored twice.
		s.log.WarnContext(ctx, "payload claims could not be read; resends in this batch are kept",
			slog.String("error", err.Error()))
		return skip
	}
	var claims []Claim
	for i, record := range records {
		if digests[i] == "" {
			continue
		}
		self := Owner{Topic: record.Topic, Partition: record.Partition, Offset: record.Offset}
		if owner, claimed := owners[digests[i]]; claimed {
			skip[i] = owner != self
			continue
		}
		// The first in this batch claims it; a copy later in the same batch is a
		// resend of that one.
		owners[digests[i]] = self
		claims = append(claims, Claim{Digest: digests[i], Owner: self})
	}
	if len(claims) > 0 {
		// A failed claim keeps the records; only a later resend of them goes
		// undetected. What earlier claims proved is still skipped.
		if err := s.claims.Claim(ctx, claims); err != nil {
			s.log.WarnContext(ctx, "payload claims could not be written; a resend of this batch would be kept",
				slog.String("error", err.Error()))
		}
	}
	for _, skipped := range skip {
		if skipped {
			Skipped.Inc()
		}
	}
	return skip
}

// digestOf is the record's digest, or "" when it carries none or one that is not
// a SHA-256 in hex — the only shape the receiver writes.
func digestOf(record *kgo.Record) string {
	for _, header := range record.Headers {
		if header.Key == DigestHeader && isDigest(header.Value) {
			return string(header.Value)
		}
	}
	return ""
}

func isDigest(value []byte) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	_, err := hex.DecodeString(string(value))
	return err == nil
}

// Over is the skipper every pipeline consumes with, over the worker's ClickHouse
// pool. A nil pool — a test without a store — skips nothing.
func Over(pool *chpool.Pool, log *slog.Logger) consume.Resends {
	if pool == nil {
		return nil
	}
	return New(NewClickHouse(pool), log)
}
