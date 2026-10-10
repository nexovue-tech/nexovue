package metricwriting

import (
	"context"

	"github.com/ClickHouse/ch-go/proto"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// The five tables, in the fixed order every chunk is written in.
const (
	tableGauge = iota
	tableSum
	tableHistogram
	tableExpHistogram
	tableSummary
	tableCount
)

var tableNames = [tableCount]string{gaugeTable, sumTable, histogramTable, expHistogramTable, summaryTable}

// heldMetric is one record and the rows it put in the five blocks.
type heldMetric struct {
	record *kgo.Record
	rows   rows
}

// count is how many rows this record has in one table.
func (r rows) count(table int) int {
	switch table {
	case tableGauge:
		return len(r.gauge)
	case tableSum:
		return len(r.sum)
	case tableHistogram:
		return len(r.histogram)
	case tableExpHistogram:
		return len(r.expHistogram)
	default:
		return len(r.summary)
	}
}

// writeTables writes the chunk one table at a time, in the fixed order, and
// narrows a refusal on the table that refused it.
//
// Narrowing per table, not per chunk, is what keeps it from writing anything
// twice. A refusal on table k leaves the tables before k written for the whole
// chunk; only table k is retried in halves to find the records it refuses, those
// are parked, and the tables after k are written for everyone else. A parked
// record therefore keeps the rows its earlier tables took — as it did when the
// whole chunk was parked — and a replay from the dead-letter topic would write
// those again.
//
// insertAll writes a table from the blocks the chunk accumulated; insertSome
// rebuilds one from a subset of the records, which is only needed once a record
// has been parked and the accumulated blocks no longer describe what to write.
// It reports the records parked and the rows they did not store.
func writeTables(
	ctx context.Context,
	held []heldMetric,
	insertAll func(ctx context.Context, table int) error,
	insertSome func(ctx context.Context, table int, subset []heldMetric) error,
	park func(context.Context, *kgo.Record, error) error,
) (parked, unstored int, err error) {
	active := held
	accumulated := true
	for table := range tableCount {
		var refusal error
		if accumulated {
			refusal = insertAll(ctx, table)
		} else {
			refusal = insertSome(ctx, table, active)
		}
		if refusal == nil {
			continue
		}
		if !consume.IsPermanent(refusal) {
			return parked, unstored, refusal
		}

		var refused []heldMetric
		err := consume.Narrow(ctx, active, refusal,
			func(ctx context.Context, subset []heldMetric) error { return insertSome(ctx, table, subset) },
			func(ctx context.Context, h heldMetric, cause error) error {
				if err := park(ctx, h.record, cause); err != nil {
					return err
				}
				refused = append(refused, h)
				return nil
			})
		if err != nil {
			return parked, unstored, err
		}
		for _, h := range refused {
			for later := table; later < tableCount; later++ {
				unstored += h.rows.count(later)
			}
		}
		parked += len(refused)
		active = without(active, refused)
		accumulated = false
	}
	return parked, unstored, nil
}

func without(held, gone []heldMetric) []heldMetric {
	kept := make([]heldMetric, 0, len(held)-len(gone))
	for _, h := range held {
		parked := false
		for _, g := range gone {
			if g.record == h.record {
				parked = true
				break
			}
		}
		if !parked {
			kept = append(kept, h)
		}
	}
	return kept
}

// insertAccumulated writes one table from the chunk's own blocks.
func (p *Pipeline) insertAccumulated(ctx context.Context, table int) error {
	rows, columns := p.blockFor(table)
	return p.insertUnderRetry(ctx, table, rows, columns)
}

// insertSubset rebuilds one table's block from a subset of the records and
// writes it.
func (p *Pipeline) insertSubset(ctx context.Context, table int, subset []heldMetric) error {
	var (
		rows    int
		columns proto.Input
	)
	switch table {
	case tableGauge:
		b := newGaugeBlock()
		for _, h := range subset {
			for _, row := range h.rows.gauge {
				b.append(row)
			}
		}
		rows, columns = b.rows(), b.columns
	case tableSum:
		b := newSumBlock()
		for _, h := range subset {
			for _, row := range h.rows.sum {
				b.append(row)
			}
		}
		rows, columns = b.rows(), b.columns
	case tableHistogram:
		b := newHistogramBlock()
		for _, h := range subset {
			for _, row := range h.rows.histogram {
				b.append(row)
			}
		}
		rows, columns = b.rows(), b.columns
	case tableExpHistogram:
		b := newExpHistogramBlock()
		for _, h := range subset {
			for _, row := range h.rows.expHistogram {
				b.append(row)
			}
		}
		rows, columns = b.rows(), b.columns
	default:
		b := newSummaryBlock()
		for _, h := range subset {
			for _, row := range h.rows.summary {
				b.append(row)
			}
		}
		rows, columns = b.rows(), b.columns
	}
	return p.insertUnderRetry(ctx, table, rows, columns)
}

func (p *Pipeline) blockFor(table int) (int, proto.Input) {
	switch table {
	case tableGauge:
		return p.gauge.rows(), p.gauge.columns
	case tableSum:
		return p.sum.rows(), p.sum.columns
	case tableHistogram:
		return p.histogram.rows(), p.histogram.columns
	case tableExpHistogram:
		return p.expHistogram.rows(), p.expHistogram.columns
	default:
		return p.summary.rows(), p.summary.columns
	}
}

// insertUnderRetry writes one table's rows, sending nothing for an empty block —
// a metric with no data points leaves no row, not a row of zeros.
func (p *Pipeline) insertUnderRetry(ctx context.Context, table, rows int, columns proto.Input) error {
	if rows == 0 {
		return nil
	}
	name := tableNames[table]
	return p.loop.Retry(ctx, "insert "+name, func(callCtx context.Context) error {
		return p.writer.insert(callCtx, name, rows, columns)
	})
}
