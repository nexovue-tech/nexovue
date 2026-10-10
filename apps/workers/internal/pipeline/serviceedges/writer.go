package serviceedges

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/insertdedup"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// edgesTable is the insert target, named once so the statement and the column
// list cannot drift apart.
const edgesTable = "service_edges_1m"

// inputColumns is the shape streamed over the native protocol. It is not the
// table's shape: `latency_quantiles` is an AggregateFunction, and no client
// builds one of those — the state's serialised form belongs to the server. So
// the durations travel as an array and the *server* folds them into the state
// on the way in, which is the same `-State` construction a materialized view
// would do.
const inputColumns = "tenant_id String, derived_retention_days UInt16, project_id String, " +
	"client_service String, server_service String, server_peer_kind String, edge_kind String, " +
	"minute DateTime, call_count UInt64, error_count UInt64, latency_ms Array(Float64)"

// targetColumns names where each selected expression lands. Without it the
// server matches on position, and a column added by a later ALTER lands at the
// end of the table rather than where this statement writes it — which is how
// `server_peer_kind` briefly tried to become the quantile state. Naming them
// costs one line and makes the insert survive the next ALTER.
const targetColumns = "(tenant_id, derived_retention_days, project_id, client_service, " +
	"server_service, server_peer_kind, edge_kind, minute, call_count, error_count, " +
	"latency_quantiles)"

// insertStatement folds each row's durations into the quantile state the table
// stores. arrayReduce applies the aggregate within one row, so the insert needs
// no GROUP BY: the accumulator has already grouped.
//
// The trailing FORMAT names how the rows that follow the statement are encoded.
// A plain `INSERT ... VALUES` implies it; an INSERT that reads from `input()`
// does not, and ClickHouse rejects the statement without it.
const insertStatement = "INSERT INTO " + edgesTable + " " + targetColumns +
	" SELECT tenant_id, derived_retention_days, " +
	"project_id, client_service, server_service, server_peer_kind, edge_kind, minute, call_count, " +
	"error_count, arrayReduce('quantilesState(0.5, 0.95, 0.99)', latency_ms) FROM input('" +
	inputColumns + "') FORMAT Native"

// block is one insert's worth of edges, held column by column. It is reused
// across flushes: resetting keeps the capacity.
type block struct {
	tenantID       proto.ColStr
	retentionDays  proto.ColUInt16
	projectID      proto.ColStr
	clientService  proto.ColStr
	serverService  proto.ColStr
	serverPeerKind proto.ColStr
	edgeKind       proto.ColStr
	minute         proto.ColDateTime
	callCount      proto.ColUInt64
	errorCount     proto.ColUInt64
	latencyMS      *proto.ColArr[float64]

	columns proto.Input
}

func newBlock() *block {
	b := &block{latencyMS: proto.NewArray[float64](new(proto.ColFloat64))}
	b.columns = proto.Input{
		{Name: "tenant_id", Data: &b.tenantID},
		{Name: "derived_retention_days", Data: &b.retentionDays},
		{Name: "project_id", Data: &b.projectID},
		{Name: "client_service", Data: &b.clientService},
		{Name: "server_service", Data: &b.serverService},
		{Name: "server_peer_kind", Data: &b.serverPeerKind},
		{Name: "edge_kind", Data: &b.edgeKind},
		{Name: "minute", Data: &b.minute},
		{Name: "call_count", Data: &b.callCount},
		{Name: "error_count", Data: &b.errorCount},
		{Name: "latency_ms", Data: b.latencyMS},
	}
	return b
}

func (b *block) append(edge Edge) {
	b.tenantID.Append(edge.TenantID)
	b.retentionDays.Append(edge.RetentionDays)
	b.projectID.Append(edge.ProjectID)
	b.clientService.Append(edge.ClientService)
	b.serverService.Append(edge.ServerService)
	b.serverPeerKind.Append(edge.ServerPeerKind)
	b.edgeKind.Append(edge.Kind)
	b.minute.Append(edge.Minute)
	b.callCount.Append(edge.CallCount)
	b.errorCount.Append(edge.ErrorCount)
	b.latencyMS.Append(edge.LatencyMS)
}

func (b *block) rows() int { return b.tenantID.Rows() }

func (b *block) reset() { b.columns.Reset() }

// deduplicationToken is what makes a retried insert recognisable as one. 000016
// added a deduplication window for this table believing the store would hash
// the block; it does not for an insert that reads from `input()`, so without
// the token a retry after a lost acknowledgement doubled every edge it carried.
// Measured — see the insertdedup package.
func deduplicationToken(edges []Edge) string {
	digest := insertdedup.NewDigest()
	for _, edge := range edges {
		digest.AddRow(edge.Minute.UTC(), edge.TenantID, edge.ProjectID, edge.RetentionDays,
			edge.ClientService, edge.ServerService, edge.ServerPeerKind, edge.Kind,
			edge.CallCount, edge.ErrorCount, edge.LatencyMS)
	}
	return digest.Token()
}

// Writer inserts derived edges into ClickHouse.
type Writer struct {
	pool  *chpool.Pool
	block *block
}

// NewWriter returns a writer over an existing pool. The caller owns the pool.
func NewWriter(pool *chpool.Pool) *Writer {
	return &Writer{pool: pool, block: newBlock()}
}

// Insert writes the edges as one columnar batch. It returns an error rather than
// retrying: the caller holds the Kafka offsets and commits only once this has
// succeeded, so a failure is retried by re-consuming.
func (w *Writer) Insert(ctx context.Context, edges []Edge) error {
	if len(edges) == 0 {
		return nil
	}
	w.block.reset()
	for _, edge := range edges {
		w.block.append(edge)
	}

	query := ch.Query{
		Body:     insertStatement,
		Input:    w.block.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: deduplicationToken(edges)}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d service edges: %w", w.block.rows(), err))
	}
	return nil
}
