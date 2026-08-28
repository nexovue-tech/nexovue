//go:build schemaprobe

package metricwriting

import (
	"context"
	"os"
	"testing"

	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/proto"
)

// probeTenant marks the rows this probe writes so they can be removed again.
const probeTenant = "__p1_29_probe__"

// TestBlocksRoundTripThroughTheServer writes one row into each of the five
// tables, reads back the parts a header check cannot prove — the Nested arrays
// and the Nullable optionals — and then deletes what it wrote.
//
// Behind a build tag: it needs a running ClickHouse, which `go test ./...` does
// not have. Run it with
//
//	PROBE_CH_ADDR=127.0.0.1:19000 go test -tags schemaprobe ./internal/pipeline/metricwriting/
func TestBlocksRoundTripThroughTheServer(t *testing.T) {
	ctx := context.Background()
	client, err := ch.Dial(ctx, ch.Options{
		Address:  os.Getenv("PROBE_CH_ADDR"),
		Database: "nexovue",
		User:     "nexovue",
		Password: "nexovue_dev",
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	// LIFO: the delete runs before the connection closes.
	t.Cleanup(func() { _ = client.Close() })
	t.Cleanup(func() { deleteProbeRows(t, client) })

	for _, subject := range probeBlocks() {
		name := subject.block.table()
		subject.appendOne()
		err := client.Do(ctx, ch.Query{
			Body:     subject.input.Into(name),
			Input:    subject.input,
			Settings: []ch.Setting{{Key: jsonAsStringSetting, Value: "1"}},
		})
		if err != nil {
			t.Errorf("%s rejected the block: %v", name, err)
			continue
		}
		t.Logf("%s accepted one row across %d columns", name, len(subject.input))
	}

	// The parts a zero-row header check cannot reach.
	assertScalar(t, client,
		"SELECT toUInt64(length(exemplars.value)) AS v FROM metrics_gauge WHERE tenant_id = '"+probeTenant+"'",
		2, "gauge exemplar array length")
	assertScalar(t, client,
		"SELECT toUInt64(length(bucket_counts)) AS v FROM metrics_histogram WHERE tenant_id = '"+probeTenant+"'",
		2, "histogram bucket_counts length")
	assertScalar(t, client,
		"SELECT toUInt64(sum IS NULL) AS v FROM metrics_histogram WHERE tenant_id = '"+probeTenant+"'",
		1, "histogram sum left absent")
	assertScalar(t, client,
		"SELECT toUInt64(length(quantiles.quantile)) AS v FROM metrics_summary WHERE tenant_id = '"+probeTenant+"'",
		1, "summary quantile array length")
}

// probeBlocks is blocks() with the probe tenant stamped on every row, and a
// gauge carrying exemplars so the Nested group is exercised.
func probeBlocks() []blockUnderTest {
	subjects := blocksWith(probePoint())
	gauge := subjects[0].block.(*gaugeBlock)
	subjects[0].appendOne = func() {
		row := probePoint()
		row.Exemplars = exemplars{
			Timestamps: []time.Time{testTime, testTime},
			Values:     []float64{1, 2},
			TraceIDs:   [][]byte{hexID(nil, traceIDHexLen), hexID(nil, traceIDHexLen)},
			SpanIDs:    [][]byte{hexID(nil, spanIDHexLen), hexID(nil, spanIDHexLen)},
		}
		gauge.append(gaugeRow{point: row})
	}
	return subjects
}

// probePoint stamps the tenant the cleanup deletes, and dates the row now: the
// tables expire on received_at, so a fixture timestamp older than the retention
// is dropped by TTL before anything can read it back.
func probePoint() point {
	row := testPoint()
	row.TenantID = probeTenant
	now := time.Now().UTC()
	row.Timestamp, row.StartTimestamp, row.ReceivedAt = now, now, now
	return row
}

func assertScalar(t *testing.T, client *ch.Client, query string, want uint64, what string) {
	t.Helper()
	var got proto.ColUInt64
	if err := client.Do(context.Background(), ch.Query{
		Body:   query,
		Result: proto.Results{{Name: "v", Data: &got}},
	}); err != nil {
		t.Fatalf("%s: %v", what, err)
	}
	if got.Rows() != 1 || got.Row(0) != want {
		t.Errorf("%s = %v, want %d", what, got, want)
	}
}

func deleteProbeRows(t *testing.T, client *ch.Client) {
	t.Helper()
	for _, table := range []string{
		gaugeTable, sumTable, histogramTable, expHistogramTable, summaryTable,
	} {
		err := client.Do(context.Background(), ch.Query{
			Body: "ALTER TABLE " + table + " DELETE WHERE tenant_id = '" + probeTenant + "'",
			Settings: []ch.Setting{
				{Key: "mutations_sync", Value: "2"},
			},
		})
		if err != nil {
			t.Errorf("cleaning %s: %v", table, err)
		}
	}
}
