package profilewriting

import (
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// table is the one insert target.
const table = "profile_samples"

// block is one insert's worth of `profile_samples`, reused across flushes so a
// steady stream stops allocating. Columns are declared in the table's order.
type block struct {
	timestamp      proto.ColDateTime64
	receivedAt     proto.ColDateTime64
	retentionDays  proto.ColUInt16
	tenantID       *proto.ColLowCardinality[string]
	projectID      *proto.ColLowCardinality[string]
	serviceName    *proto.ColLowCardinality[string]
	hostName       *proto.ColLowCardinality[string]
	serviceVersion *proto.ColLowCardinality[string]
	environment    *proto.ColLowCardinality[string]
	sampleType     *proto.ColLowCardinality[string]
	sampleUnit     *proto.ColLowCardinality[string]
	value          proto.ColInt64
	stackHash      proto.ColUInt64
	functions      *proto.ColArr[string]
	files          *proto.ColArr[string]
	lines          *proto.ColArr[uint32]
	binaries       *proto.ColArr[string]
	addresses      *proto.ColArr[uint64]
	traceID        proto.ColFixedStr
	spanID         proto.ColFixedStr
	resourceAttrs  proto.ColJSONStr
	attrs          proto.ColJSONStr

	columns proto.Input
}

func newBlock() *block {
	b := &block{
		tenantID:       new(proto.ColStr).LowCardinality(),
		projectID:      new(proto.ColStr).LowCardinality(),
		serviceName:    new(proto.ColStr).LowCardinality(),
		hostName:       new(proto.ColStr).LowCardinality(),
		serviceVersion: new(proto.ColStr).LowCardinality(),
		environment:    new(proto.ColStr).LowCardinality(),
		sampleType:     new(proto.ColStr).LowCardinality(),
		sampleUnit:     new(proto.ColStr).LowCardinality(),
		functions:      new(proto.ColStr).LowCardinality().Array(),
		files:          new(proto.ColStr).LowCardinality().Array(),
		lines:          new(proto.ColUInt32).Array(),
		binaries:       new(proto.ColStr).LowCardinality().Array(),
		addresses:      new(proto.ColUInt64).Array(),
		traceID:        proto.ColFixedStr{Size: otlpvalue.TraceIDHexLen},
		spanID:         proto.ColFixedStr{Size: otlpvalue.SpanIDHexLen},
	}
	// The table declares these precisions; the columns must say the same or ch-go
	// tells the server the wrong type.
	b.timestamp.WithPrecision(proto.PrecisionNano)
	b.receivedAt.WithPrecision(proto.PrecisionMilli)

	b.columns = proto.Input{
		{Name: "timestamp", Data: &b.timestamp},
		{Name: "received_at", Data: &b.receivedAt},
		{Name: "retention_days", Data: &b.retentionDays},
		{Name: "tenant_id", Data: b.tenantID},
		{Name: "project_id", Data: b.projectID},
		{Name: "service_name", Data: b.serviceName},
		{Name: "host_name", Data: b.hostName},
		{Name: "service_version", Data: b.serviceVersion},
		{Name: "environment", Data: b.environment},
		{Name: "sample_type", Data: b.sampleType},
		{Name: "sample_unit", Data: b.sampleUnit},
		{Name: "value", Data: &b.value},
		{Name: "stack_hash", Data: &b.stackHash},
		{Name: "frame_functions", Data: b.functions},
		{Name: "frame_files", Data: b.files},
		{Name: "frame_lines", Data: b.lines},
		{Name: "frame_binaries", Data: b.binaries},
		{Name: "frame_addresses", Data: b.addresses},
		{Name: "trace_id", Data: &b.traceID},
		{Name: "span_id", Data: &b.spanID},
		{Name: "resource_attrs", Data: &b.resourceAttrs},
		{Name: "attrs", Data: &b.attrs},
	}
	return b
}

func (b *block) append(r row) {
	b.timestamp.Append(r.Timestamp)
	b.receivedAt.Append(r.ReceivedAt)
	b.retentionDays.Append(r.RetentionDays)
	b.tenantID.Append(r.TenantID)
	b.projectID.Append(r.ProjectID)
	b.serviceName.Append(r.ServiceName)
	b.hostName.Append(r.HostName)
	b.serviceVersion.Append(r.ServiceVersion)
	b.environment.Append(r.Environment)
	b.sampleType.Append(r.SampleType)
	b.sampleUnit.Append(r.SampleUnit)
	b.value.Append(r.Value)
	b.stackHash.Append(r.StackHash)
	b.functions.Append(r.Functions)
	b.files.Append(r.Files)
	b.lines.Append(r.Lines)
	b.binaries.Append(r.Binaries)
	b.addresses.Append(r.Addresses)
	b.traceID.Append(r.TraceID)
	b.spanID.Append(r.SpanID)
	b.resourceAttrs.Append(r.ResourceAttrs)
	b.attrs.Append(r.Attrs)
}

func (b *block) rows() int { return b.timestamp.Rows() }
func (b *block) reset()    { b.columns.Reset() }
