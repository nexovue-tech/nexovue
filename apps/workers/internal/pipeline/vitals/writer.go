package vitals

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/insertdedup"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// jsonAsStringSetting lets the `attrs` JSON column travel the native protocol
// as one JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// Writer inserts both halves of the browser door's vitals: the raw readings and
// the per-minute rollup they fold into.
//
// One type rather than two because the two inserts are one obligation. A batch
// whose readings landed but whose rollup did not would leave the screen's p75
// permanently short of rows the raw table can prove were received, and nothing
// downstream recomputes the rollup from the raw table.
type Writer struct {
	pool   *chpool.Pool
	rows   *readingBlock
	rollup *rollupBlock
}

// NewWriter returns a writer over an existing pool. The caller owns the pool.
func NewWriter(pool *chpool.Pool) *Writer {
	return &Writer{pool: pool, rows: newReadingBlock(), rollup: newRollupBlock()}
}

// Insert writes the readings and the rollup rows they produced.
//
// The raw rows go first. If the rollup insert then fails the batch is retried
// whole, and the readings that already landed are recognised as the repeat they
// are by their deduplication token — so the recoverable outcome is a duplicated
// attempt, never a reading that exists in neither table.
//
// It returns an error rather than retrying: the caller holds the Kafka offsets
// and commits only once this has succeeded, so a failure is retried by
// re-consuming.
func (w *Writer) Insert(ctx context.Context, readings []Reading, stats []Stat) error {
	if err := w.insertReadings(ctx, readings); err != nil {
		return err
	}
	return w.insertRollup(ctx, stats)
}

func (w *Writer) insertReadings(ctx context.Context, readings []Reading) error {
	if len(readings) == 0 {
		return nil
	}
	w.rows.reset()
	for _, reading := range readings {
		w.rows.append(reading)
	}

	query := ch.Query{
		Body:  readingInsert,
		Input: w.rows.columns,
		Settings: []ch.Setting{
			{Key: jsonAsStringSetting, Value: "1"},
			{Key: insertdedup.Setting, Value: readingToken(readings)},
		},
	}
	return w.do(ctx, query, fmt.Sprintf("insert %d web vitals", len(readings)))
}

func (w *Writer) insertRollup(ctx context.Context, stats []Stat) error {
	if len(stats) == 0 {
		return nil
	}
	w.rollup.reset()
	for _, stat := range stats {
		w.rollup.append(stat)
	}

	query := ch.Query{
		Body:     rollupInsert,
		Input:    w.rollup.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: rollupToken(stats)}},
	}
	return w.do(ctx, query, fmt.Sprintf("insert %d web vital rollup rows", len(stats)))
}

// do runs one insert and classifies its failure, so a refusal parks the records
// rather than retrying until the whole process gives up.
func (w *Writer) do(ctx context.Context, query ch.Query, describe string) error {
	if err := w.pool.Do(ctx, query); err != nil {
		wrapped := fmt.Errorf("%s: %w", describe, err)
		if storefailure.RefusedTheData(err) {
			return consume.AsPermanent(wrapped)
		}
		return wrapped
	}
	return nil
}

// readingToken makes a retried insert recognisable as one.
//
// The raw insert carries its own rows, which the store does hash — but only
// while the block is byte-identical, and `attrs` is a JSON document whose
// encoding is not ours to guarantee. Deriving the token from the readings
// themselves takes the guarantee back: the same records re-derived from the
// same offsets produce the same token whatever the encoder did.
func readingToken(readings []Reading) string {
	digest := insertdedup.NewDigest()
	for _, reading := range readings {
		digest.AddRow(reading.ReceivedAt.UTC(), reading.TenantID, reading.ProjectID,
			reading.SessionID, reading.VitalName, reading.VitalID, reading.Value,
			reading.URLPath, reading.DeviceClass, reading.Country)
	}
	return digest.Token()
}

// rollupToken does the same for the rollup, where it is not an improvement but
// the only thing there is: the statement reads from `input()`, and that form is
// not covered by the store's own block hashing — see the insertdedup package,
// where it is measured.
func rollupToken(stats []Stat) string {
	digest := insertdedup.NewDigest()
	for _, stat := range stats {
		digest.AddRow(stat.Minute.UTC(), stat.TenantID, stat.ProjectID,
			stat.DerivedRetentionDays, stat.VitalName, stat.URLPath,
			stat.DeviceClass, stat.Country, stat.ReadingCount, stat.Values)
	}
	return digest.Token()
}
