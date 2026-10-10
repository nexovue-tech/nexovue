package consume

import (
	"context"
	"errors"
	"log/slog"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// Park sends a message the stages cannot process to the dead-letter topic. Its
// offset is only committed with the rest of its batch, so a park that fails is
// retried by re-consuming rather than silently dropping the message.
//
// The message is attributed to the tenant and project the envelope named. That
// is not decoration: a parked message skipped the mask stage by construction,
// so the dead-letter topic holds unmasked customer data, and an exposure nobody
// can attribute is one nobody can disclose or delete.
func (l *Loop[S]) Park(ctx context.Context, record *kgo.Record, cause error) error {
	details := attribution(cause)
	if err := l.Retry(ctx, "park to dead-letter", func(callCtx context.Context) error {
		return l.parked.Park(callCtx, record, cause.Error(), details)
	}); err != nil {
		return err
	}
	l.log.WarnContext(ctx, "parked an undecodable message",
		slog.String("topic", record.Topic),
		slog.Int("partition", int(record.Partition)),
		slog.Int64("offset", record.Offset),
		slog.String("dlq_topic", l.settings.DLQTopic),
		slog.String("tenant_id", details.TenantID),
		slog.String("project_id", details.ProjectID),
		slog.String("error", cause.Error()),
	)
	return nil
}

// attribution reads the coordinates the decoder established. An error carrying
// none means the bytes were not our envelope at all, and the parked message is
// honestly unattributed rather than attributed to a guess.
func attribution(cause error) dlq.Context {
	var rejection *Rejection
	if !errors.As(cause, &rejection) {
		return dlq.Context{}
	}
	return dlq.Context{
		IngestID:  rejection.IngestID,
		TenantID:  rejection.TenantID,
		ProjectID: rejection.ProjectID,
	}
}

// ParkRow parks one row the store refused when no source message can be named —
// a span released by the sampler batches after the records that carried it
// were let go (commit.go). The row is already masked, which is why it, and not
// an unmasked envelope, is what is kept.
//
// The source offset is recorded as -1: unknown, not zero.
func (l *Loop[S]) ParkRow(
	ctx context.Context,
	sourceTopic string,
	key, row []byte,
	tenantID, projectID string,
	cause error,
) error {
	record := &kgo.Record{Topic: sourceTopic, Partition: -1, Offset: -1, Key: key, Value: row}
	details := dlq.Context{TenantID: tenantID, ProjectID: projectID, Payload: dlq.PayloadRow}
	if err := l.Retry(ctx, "park refused row to dead-letter", func(callCtx context.Context) error {
		return l.parked.Park(callCtx, record, cause.Error(), details)
	}); err != nil {
		return err
	}
	l.log.WarnContext(ctx, "parked a row the store refused",
		slog.String("topic", sourceTopic),
		slog.String("dlq_topic", l.settings.DLQTopic),
		slog.String("tenant_id", tenantID),
		slog.String("project_id", projectID),
		slog.String("error", cause.Error()),
	)
	return nil
}
