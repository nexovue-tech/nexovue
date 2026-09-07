package vitals

import (
	"github.com/ClickHouse/ch-go/proto"
)

// The insert targets, named once so each statement and its column list cannot
// drift apart.
const (
	readingsTable = "web_vitals"
	rollupTable   = "web_vitals_1m"
)

// readingColumns names the target columns rather than relying on their order in
// the table, so a column added to `web_vitals` cannot silently shift this insert
// onto the wrong ones.
const readingColumns = "(timestamp, received_at, retention_days, tenant_id, project_id, " +
	"session_id, vital_name, value, delta, rating, navigation_type, url_path, " +
	"route, device_class, country, attrs)"

const readingInsert = "INSERT INTO " + readingsTable + " " + readingColumns + " VALUES"

// readingBlock is one insert's worth of raw readings held column by column,
// which is the shape ch-go writes and ClickHouse stores. A block is reused
// across flushes: resetting keeps the capacity, so a steady stream stops
// allocating.
type readingBlock struct {
	timestamp      proto.ColDateTime64
	receivedAt     proto.ColDateTime64
	retentionDays  proto.ColUInt16
	tenantID       *proto.ColLowCardinality[string]
	projectID      *proto.ColLowCardinality[string]
	sessionID      proto.ColStr
	vitalName      *proto.ColLowCardinality[string]
	value          proto.ColFloat64
	delta          proto.ColFloat64
	rating         *proto.ColLowCardinality[string]
	navigationType *proto.ColLowCardinality[string]
	urlPath        proto.ColStr
	route          *proto.ColLowCardinality[string]
	deviceClass    *proto.ColLowCardinality[string]
	country        *proto.ColLowCardinality[string]
	attrs          proto.ColJSONStr

	columns proto.Input
}

func newReadingBlock() *readingBlock {
	block := &readingBlock{
		tenantID:       new(proto.ColStr).LowCardinality(),
		projectID:      new(proto.ColStr).LowCardinality(),
		vitalName:      new(proto.ColStr).LowCardinality(),
		rating:         new(proto.ColStr).LowCardinality(),
		navigationType: new(proto.ColStr).LowCardinality(),
		route:          new(proto.ColStr).LowCardinality(),
		deviceClass:    new(proto.ColStr).LowCardinality(),
		country:        new(proto.ColStr).LowCardinality(),
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
		{Name: "session_id", Data: &block.sessionID},
		{Name: "vital_name", Data: block.vitalName},
		{Name: "value", Data: &block.value},
		{Name: "delta", Data: &block.delta},
		{Name: "rating", Data: block.rating},
		{Name: "navigation_type", Data: block.navigationType},
		{Name: "url_path", Data: &block.urlPath},
		{Name: "route", Data: block.route},
		{Name: "device_class", Data: block.deviceClass},
		{Name: "country", Data: block.country},
		{Name: "attrs", Data: &block.attrs},
	}
	return block
}

func (b *readingBlock) append(reading Reading) {
	b.timestamp.Append(reading.Timestamp)
	b.receivedAt.Append(reading.ReceivedAt)
	b.retentionDays.Append(reading.RetentionDays)
	b.tenantID.Append(reading.TenantID)
	b.projectID.Append(reading.ProjectID)
	b.sessionID.Append(reading.SessionID)
	b.vitalName.Append(reading.VitalName)
	b.value.Append(reading.Value)
	b.delta.Append(reading.Delta)
	b.rating.Append(reading.Rating)
	b.navigationType.Append(reading.NavigationType)
	b.urlPath.Append(reading.URLPath)
	b.route.Append(reading.Route)
	b.deviceClass.Append(reading.DeviceClass)
	b.country.Append(reading.Country)
	b.attrs.Append(reading.Attrs)
}

func (b *readingBlock) reset() { b.columns.Reset() }

// rollupInput is the shape streamed over the native protocol. It is not the
// table's shape: `value_quantiles` is an AggregateFunction, and no client builds
// one of those — the state's serialised form belongs to the server. So the
// readings travel as an array and the server folds them into the state on the
// way in.
const rollupInput = "tenant_id String, derived_retention_days UInt16, project_id String, " +
	"vital_name String, route String, url_path String, device_class String, country String, " +
	"minute DateTime, reading_count UInt64, values Array(Float64)"

const rollupColumns = "(tenant_id, derived_retention_days, project_id, vital_name, " +
	"route, url_path, device_class, country, minute, reading_count, value_quantiles)"

// rollupInsert folds each row's readings into the quantile state the table
// stores. arrayReduce applies the aggregate within one row, so the insert needs
// no GROUP BY: the rollup has already grouped.
//
// The levels are the column's own — `quantilesMerge` refuses a state whose
// levels differ from the stored ones, so a read would fail loudly rather than
// return a wrong percentile. They are the same three the API's queries.py
// spells, and migration 000031 declared.
//
// The trailing FORMAT names how the rows that follow the statement are encoded.
// A plain `INSERT ... VALUES` implies it; an INSERT that reads from `input()`
// does not, and ClickHouse rejects the statement without it.
const rollupInsert = "INSERT INTO " + rollupTable + " " + rollupColumns +
	" SELECT tenant_id, derived_retention_days, project_id, vital_name, route, url_path, " +
	"device_class, country, minute, reading_count, " +
	"arrayReduce('quantilesState(0.5, 0.75, 0.95)', values) " +
	"FROM input('" + rollupInput + "') FORMAT Native"

// rollupBlock is one insert's worth of rollup rows, held column by column.
type rollupBlock struct {
	tenantID      proto.ColStr
	retentionDays proto.ColUInt16
	projectID     proto.ColStr
	vitalName     proto.ColStr
	route         proto.ColStr
	urlPath       proto.ColStr
	deviceClass   proto.ColStr
	country       proto.ColStr
	minute        proto.ColDateTime
	readingCount  proto.ColUInt64
	values        *proto.ColArr[float64]

	columns proto.Input
}

func newRollupBlock() *rollupBlock {
	block := &rollupBlock{values: proto.NewArray(new(proto.ColFloat64))}
	block.columns = proto.Input{
		{Name: "tenant_id", Data: &block.tenantID},
		{Name: "derived_retention_days", Data: &block.retentionDays},
		{Name: "project_id", Data: &block.projectID},
		{Name: "vital_name", Data: &block.vitalName},
		{Name: "route", Data: &block.route},
		{Name: "url_path", Data: &block.urlPath},
		{Name: "device_class", Data: &block.deviceClass},
		{Name: "country", Data: &block.country},
		{Name: "minute", Data: &block.minute},
		{Name: "reading_count", Data: &block.readingCount},
		{Name: "values", Data: block.values},
	}
	return block
}

func (b *rollupBlock) append(stat Stat) {
	b.tenantID.Append(stat.TenantID)
	b.retentionDays.Append(stat.DerivedRetentionDays)
	b.projectID.Append(stat.ProjectID)
	b.vitalName.Append(stat.VitalName)
	b.route.Append(stat.Route)
	b.urlPath.Append(stat.URLPath)
	b.deviceClass.Append(stat.DeviceClass)
	b.country.Append(stat.Country)
	b.minute.Append(stat.Minute)
	b.readingCount.Append(stat.ReadingCount)
	b.values.Append(stat.Values)
}

func (b *rollupBlock) reset() { b.columns.Reset() }
