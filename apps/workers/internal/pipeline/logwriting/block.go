package logwriting

import (
	"github.com/ClickHouse/ch-go/proto"
)

// logsTable is the insert target, named once so the statement and the column
// list cannot drift apart.
const logsTable = "logs"

// logBlock is one insert's worth of rows held column by column, which is the
// shape ch-go writes and ClickHouse stores. A block is reused across flushes:
// resetting keeps the capacity, so a steady stream stops allocating.
type logBlock struct {
	timestamp        proto.ColDateTime64
	receivedAt       proto.ColDateTime64
	retentionDays    proto.ColUInt16
	tenantID         *proto.ColLowCardinality[string]
	projectID        *proto.ColLowCardinality[string]
	traceID          proto.ColFixedStr
	spanID           proto.ColFixedStr
	serviceName      *proto.ColLowCardinality[string]
	severity         *proto.ColLowCardinality[string]
	severityNumber   proto.ColInt32
	body             proto.ColStr
	bodyStructured   proto.ColJSONStr
	resourceAttrs    proto.ColJSONStr
	logAttrs         proto.ColJSONStr
	exceptionType    *proto.ColLowCardinality[string]
	exceptionMessage proto.ColStr
	timestampSource  *proto.ColLowCardinality[string]

	// columns names each column exactly as the table declares it, so the server
	// matches on name rather than on position. Built once; it is also the
	// statement's column list.
	columns proto.Input
}

func newLogBlock() *logBlock {
	block := &logBlock{
		tenantID:        new(proto.ColStr).LowCardinality(),
		projectID:       new(proto.ColStr).LowCardinality(),
		traceID:         proto.ColFixedStr{Size: traceIDHexLen},
		spanID:          proto.ColFixedStr{Size: spanIDHexLen},
		serviceName:     new(proto.ColStr).LowCardinality(),
		severity:        new(proto.ColStr).LowCardinality(),
		exceptionType:   new(proto.ColStr).LowCardinality(),
		timestampSource: new(proto.ColStr).LowCardinality(),
	}
	// The column carries its own precision; without it ch-go cannot encode a
	// time and the server would be told the wrong type.
	block.timestamp.WithPrecision(proto.PrecisionNano)
	block.receivedAt.WithPrecision(proto.PrecisionNano)

	block.columns = proto.Input{
		{Name: "timestamp", Data: &block.timestamp},
		{Name: "received_at", Data: &block.receivedAt},
		{Name: "retention_days", Data: &block.retentionDays},
		{Name: "tenant_id", Data: block.tenantID},
		{Name: "project_id", Data: block.projectID},
		{Name: "trace_id", Data: &block.traceID},
		{Name: "span_id", Data: &block.spanID},
		{Name: "service_name", Data: block.serviceName},
		{Name: "severity", Data: block.severity},
		{Name: "severity_number", Data: &block.severityNumber},
		{Name: "body", Data: &block.body},
		{Name: "body_structured", Data: &block.bodyStructured},
		{Name: "resource_attrs", Data: &block.resourceAttrs},
		{Name: "log_attrs", Data: &block.logAttrs},
		{Name: "exception_type", Data: block.exceptionType},
		{Name: "exception_message", Data: &block.exceptionMessage},
		{Name: "timestamp_source", Data: block.timestampSource},
	}
	return block
}

func (b *logBlock) append(row logRow) {
	b.timestamp.Append(row.Timestamp)
	b.receivedAt.Append(row.ReceivedAt)
	b.retentionDays.Append(row.RetentionDays)
	b.tenantID.Append(row.TenantID)
	b.projectID.Append(row.ProjectID)
	b.traceID.Append(row.TraceID)
	b.spanID.Append(row.SpanID)
	b.serviceName.Append(row.ServiceName)
	b.severity.Append(row.Severity)
	b.severityNumber.Append(row.SeverityNumber)
	b.body.Append(row.Body)
	b.bodyStructured.Append(row.BodyStructured)
	b.resourceAttrs.Append(row.ResourceAttrs)
	b.logAttrs.Append(row.LogAttrs)
	b.exceptionType.Append(row.ExceptionType)
	b.exceptionMessage.Append(row.ExceptionMessage)
	b.timestampSource.Append(row.TimestampSource)
}

func (b *logBlock) rows() int { return b.timestamp.Rows() }

func (b *logBlock) reset() { b.columns.Reset() }

// statement is the INSERT this block's columns belong to.
func (b *logBlock) statement() string { return b.columns.Into(logsTable) }
