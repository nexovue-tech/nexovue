package traceassembly

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// What a span insert does when ClickHouse refuses the rows themselves.
//
// The log, metric and profile pipelines park the records whose rows were
// refused. This one cannot: the rows leave the sampling buffer batches after the
// records that carried them, and the loop keeps only their positions
// (consume/commit.go). So the refusal is narrowed to its rows instead — the
// block is split and each half inserted again, down to single rows — every row
// the store accepts is written, and each row it refuses is parked by itself,
// already masked, on the spans dead-letter topic.
//
// Before this, one refused span ended the pipeline after a two-minute retry,
// and every signal in the process with it (docs/changes/
// ingest-cannot-be-knocked-over).

// writeSpans inserts the block under the retry budget and, on a refusal,
// salvages it row by row.
func (p *Pipeline) writeSpans(ctx context.Context) error {
	err := p.loop.Retry(ctx, "insert spans", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, p.block)
	})
	if err == nil || !consume.IsPermanent(err) {
		return err
	}
	p.log.ErrorContext(ctx, "the store refused a span insert; narrowing it to the refused rows",
		slog.Int("spans", len(p.pending)),
		slog.String("error", err.Error()),
	)
	return p.salvage(ctx, p.pending, err)
}

// salvage writes what the store accepts of rows and parks what it refuses.
func (p *Pipeline) salvage(ctx context.Context, rows []spanRow, refusal error) error {
	return consume.Narrow(ctx, rows, refusal, p.insertRows, p.parkSpan)
}

// insertRows writes rows as one block under the retry budget.
func (p *Pipeline) insertRows(ctx context.Context, rows []spanRow) error {
	scratch := newSpanBlock()
	for _, row := range rows {
		scratch.append(row)
	}
	return p.loop.Retry(ctx, "insert spans", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, scratch)
	})
}

// parkSpan sets one refused row aside on the dead-letter topic.
//
// The trace's summary has already counted it — summaries see every row the
// sampler released, before the insert — so that trace's span count runs one
// high. Accepted: the alternative is unwinding a merged aggregate for a row
// that is still kept, just not in `spans`.
func (p *Pipeline) parkSpan(ctx context.Context, row spanRow, refusal error) error {
	payload, err := json.Marshal(row)
	if err != nil {
		return fmt.Errorf("encoding a refused span to park it: %w", err)
	}
	if err := p.loop.ParkRow(ctx, p.topic, row.TraceID, payload,
		row.TenantID, row.ProjectID, refusal); err != nil {
		return err
	}
	p.spansParked++
	return nil
}

// stepOverRefusal turns a derived insert's outcome into what it costs: nil when
// it was stored or refused, the error when it is worth retrying.
//
// A derived row is a sum or an index over spans, not a customer's own record.
// Refused, it is counted and stepped over so the settlement still completes and
// the offsets can move — a refusal there costs a chart one point, where ending
// the pipeline cost every signal for every tenant.
func (p *Pipeline) stepOverRefusal(ctx context.Context, what string, rows int, err error) error {
	if err == nil || !consume.IsPermanent(err) {
		return err
	}
	p.derivedRowsRefused += uint64(max(rows, 0)) //nolint:gosec // clamped to non-negative
	p.log.ErrorContext(ctx, "the store refused derived rows; counted and stepped over",
		slog.String("table", what),
		slog.Int("rows", rows),
		slog.String("error", err.Error()),
	)
	return nil
}
