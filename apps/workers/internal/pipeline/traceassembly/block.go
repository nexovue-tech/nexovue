package traceassembly

import (
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// spansTable is the insert target, named once so the statement and the column
// list cannot drift apart.
const spansTable = "spans"

// spanBlock is one insert's worth of rows held column by column, which is the
// shape ch-go writes and ClickHouse stores. A block is reused across flushes:
// resetting keeps the capacity, so a steady stream stops allocating.
type spanBlock struct {
	timestamp         proto.ColDateTime64
	receivedAt        proto.ColDateTime64
	retentionDays     proto.ColUInt16
	derivedRetention  proto.ColUInt16
	tenantID          *proto.ColLowCardinality[string]
	projectID         *proto.ColLowCardinality[string]
	traceID           proto.ColFixedStr
	spanID            proto.ColFixedStr
	parentSpanID      proto.ColFixedStr
	serviceName       *proto.ColLowCardinality[string]
	spanName          *proto.ColLowCardinality[string]
	spanKind          *proto.ColLowCardinality[string]
	durationMS        proto.ColFloat64
	statusCode        *proto.ColLowCardinality[string]
	statusMessage     proto.ColStr
	httpMethod        *proto.ColLowCardinality[string]
	httpRoute         *proto.ColLowCardinality[string]
	httpStatus        proto.ColUInt16
	rpcSystem         *proto.ColLowCardinality[string]
	rpcMethod         *proto.ColLowCardinality[string]
	rpcStatus         *proto.ColLowCardinality[string]
	deploymentVersion *proto.ColLowCardinality[string]
	resourceAttrs     proto.ColJSONStr
	spanAttrs         proto.ColJSONStr
	events            proto.ColStr

	// columns names each column exactly as the table declares it, so the server
	// matches on name rather than on position. Built once; it is also the
	// statement's column list.
	columns proto.Input
}

func newSpanBlock() *spanBlock {
	block := &spanBlock{
		tenantID:          new(proto.ColStr).LowCardinality(),
		projectID:         new(proto.ColStr).LowCardinality(),
		traceID:           proto.ColFixedStr{Size: otlpvalue.TraceIDHexLen},
		spanID:            proto.ColFixedStr{Size: otlpvalue.SpanIDHexLen},
		parentSpanID:      proto.ColFixedStr{Size: otlpvalue.SpanIDHexLen},
		serviceName:       new(proto.ColStr).LowCardinality(),
		spanName:          new(proto.ColStr).LowCardinality(),
		spanKind:          new(proto.ColStr).LowCardinality(),
		statusCode:        new(proto.ColStr).LowCardinality(),
		httpMethod:        new(proto.ColStr).LowCardinality(),
		httpRoute:         new(proto.ColStr).LowCardinality(),
		rpcSystem:         new(proto.ColStr).LowCardinality(),
		rpcMethod:         new(proto.ColStr).LowCardinality(),
		rpcStatus:         new(proto.ColStr).LowCardinality(),
		deploymentVersion: new(proto.ColStr).LowCardinality(),
	}
	// The column carries its own precision; without it ch-go cannot encode a
	// time and the server would be told the wrong type.
	block.timestamp.WithPrecision(proto.PrecisionNano)
	block.receivedAt.WithPrecision(proto.PrecisionNano)

	block.columns = proto.Input{
		{Name: "timestamp", Data: &block.timestamp},
		{Name: "received_at", Data: &block.receivedAt},
		{Name: "retention_days", Data: &block.retentionDays},
		{Name: "derived_retention_days", Data: &block.derivedRetention},
		{Name: "tenant_id", Data: block.tenantID},
		{Name: "project_id", Data: block.projectID},
		{Name: "trace_id", Data: &block.traceID},
		{Name: "span_id", Data: &block.spanID},
		{Name: "parent_span_id", Data: &block.parentSpanID},
		{Name: "service_name", Data: block.serviceName},
		{Name: "span_name", Data: block.spanName},
		{Name: "span_kind", Data: block.spanKind},
		{Name: "duration_ms", Data: &block.durationMS},
		{Name: "status_code", Data: block.statusCode},
		{Name: "status_message", Data: &block.statusMessage},
		{Name: "http_method", Data: block.httpMethod},
		{Name: "http_route", Data: block.httpRoute},
		{Name: "http_status", Data: &block.httpStatus},
		{Name: "rpc_system", Data: block.rpcSystem},
		{Name: "rpc_method", Data: block.rpcMethod},
		{Name: "rpc_status", Data: block.rpcStatus},
		{Name: "deployment_version", Data: block.deploymentVersion},
		{Name: "resource_attrs", Data: &block.resourceAttrs},
		{Name: "span_attrs", Data: &block.spanAttrs},
		{Name: "events", Data: &block.events},
	}
	return block
}

func (b *spanBlock) append(row spanRow) {
	b.timestamp.Append(row.Timestamp)
	b.receivedAt.Append(row.ReceivedAt)
	b.retentionDays.Append(row.RetentionDays)
	b.derivedRetention.Append(row.DerivedRetentionDays)
	b.tenantID.Append(row.TenantID)
	b.projectID.Append(row.ProjectID)
	b.traceID.Append(row.TraceID)
	b.spanID.Append(row.SpanID)
	b.parentSpanID.Append(row.ParentSpanID)
	b.serviceName.Append(row.ServiceName)
	b.spanName.Append(row.SpanName)
	b.spanKind.Append(row.SpanKind)
	b.durationMS.Append(row.DurationMS)
	b.statusCode.Append(row.StatusCode)
	b.statusMessage.Append(row.StatusMessage)
	b.httpMethod.Append(row.HTTPMethod)
	b.httpRoute.Append(row.HTTPRoute)
	b.httpStatus.Append(row.HTTPStatus)
	b.rpcSystem.Append(row.RPCSystem)
	b.rpcMethod.Append(row.RPCMethod)
	b.rpcStatus.Append(row.RPCStatus)
	b.deploymentVersion.Append(row.DeploymentVersion)
	b.resourceAttrs.Append(row.ResourceAttrs)
	b.spanAttrs.Append(row.SpanAttrs)
	b.events.Append(row.Events)
}

func (b *spanBlock) rows() int { return b.timestamp.Rows() }

func (b *spanBlock) reset() { b.columns.Reset() }

// statement is the INSERT this block's columns belong to.
func (b *spanBlock) statement() string { return b.columns.Into(spansTable) }
