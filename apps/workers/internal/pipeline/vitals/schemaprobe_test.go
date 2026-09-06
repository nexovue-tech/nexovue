//go:build schemaprobe

package vitals

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// probeTenant marks the rows this probe writes so they can be removed again.
const probeTenant = "__m5_vitals_probe__"

// TestTheWriterRoundTripsThroughTheServer sends real readings through the real
// writer into both tables, then reads the p75 back the way the API reads it —
// `quantilesMerge` with the levels migration 000031 declared.
//
// A unit test cannot reach any of this. Column names, the `arrayReduce` that
// folds an array into an AggregateFunction, and whether the stored state's
// levels match the ones read back are all decided by the server.
//
// Behind a build tag: it needs a running ClickHouse, which `go test ./...` does
// not have. Run it with
//
//	PROBE_CH_ADDR=127.0.0.1:19000 go test -tags schemaprobe ./internal/pipeline/vitals/
func TestTheWriterRoundTripsThroughTheServer(t *testing.T) {
	ctx := context.Background()
	pool, err := chpool.Dial(ctx, chpool.Options{
		ClientOptions: ch.Options{
			Address:  os.Getenv("PROBE_CH_ADDR"),
			Database: "nexovue",
			User:     "nexovue",
			Password: "nexovue_dev",
		},
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	// LIFO: the delete runs before the pool closes.
	t.Cleanup(pool.Close)
	t.Cleanup(func() { deleteProbeRows(t, pool) })

	// Four readings on one page in one minute: 100, 200, 300, 400. The p75 of
	// those is 325 by ClickHouse's interpolating `quantiles`.
	now := time.Now().UTC()
	rollup := NewRollup()
	readings := make([]Reading, 0, 4)
	for i, value := range []float64{100, 200, 300, 400} {
		reading := Reading{
			Timestamp:            now,
			ReceivedAt:           now,
			RetentionDays:        30,
			DerivedRetentionDays: 395,
			TenantID:             probeTenant,
			ProjectID:            "probe-project",
			SessionID:            "probe-session",
			VitalName:            "lcp",
			VitalID:              string(rune('a' + i)),
			Value:                value,
			Delta:                value,
			Rating:               "good",
			NavigationType:       "navigate",
			URLPath:              "/checkout",
			DeviceClass:          DeviceClassMobile,
			Attrs:                `{"browser.mobile":true}`,
		}
		readings = append(readings, reading)
		rollup.Add(reading)
	}

	if err := NewWriter(pool).Insert(ctx, readings, rollup.Rows()); err != nil {
		t.Fatalf("the server refused the writer's blocks: %v", err)
	}
	t.Logf("wrote %d readings across %d columns and 1 rollup row",
		len(readings), len(newReadingBlock().columns))

	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+"'",
		4, "raw readings stored")
	assertScalar(t, pool,
		"SELECT toUInt64(sum(reading_count)) AS v FROM "+rollupTable+
			" WHERE tenant_id = '"+probeTenant+"'",
		4, "rollup reading_count")

	// The read the API performs, at the levels the column was declared with.
	// A mismatch here is what a wrong percentile on the screen would look like.
	assertScalar(t, pool,
		"SELECT toUInt64(quantilesMerge(0.5, 0.75, 0.95)(value_quantiles)[2]) AS v FROM "+
			rollupTable+" WHERE tenant_id = '"+probeTenant+"'",
		325, "p75 read back through quantilesMerge")

	// The promoted columns a screen filters on survived the round trip.
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+
			"' AND vital_name = 'lcp' AND url_path = '/checkout' AND device_class = 'mobile'",
		4, "promoted columns filterable")
}

// TestARepeatedInsertIsRecognised proves the deduplication token does what
// migration 000030 says the writer must make it do. Without it the same rollup
// rows sent twice sum in twice, and a retried batch silently doubles every p75's
// sample count.
func TestARepeatedInsertIsRecognised(t *testing.T) {
	ctx := context.Background()
	pool, err := chpool.Dial(ctx, chpool.Options{
		ClientOptions: ch.Options{
			Address:  os.Getenv("PROBE_CH_ADDR"),
			Database: "nexovue",
			User:     "nexovue",
			Password: "nexovue_dev",
		},
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(pool.Close)
	t.Cleanup(func() { deleteProbeRows(t, pool) })

	now := time.Now().UTC()
	reading := Reading{
		Timestamp: now, ReceivedAt: now, RetentionDays: 30, DerivedRetentionDays: 395,
		TenantID: probeTenant, ProjectID: "probe-project", VitalName: "inp",
		VitalID: "only", Value: 42, Delta: 42, URLPath: "/", Attrs: "{}",
	}
	rollup := NewRollup()
	rollup.Add(reading)

	writer := NewWriter(pool)
	rows := rollup.Rows()
	for attempt := range 2 {
		if err := writer.Insert(ctx, []Reading{reading}, rows); err != nil {
			t.Fatalf("insert %d: %v", attempt+1, err)
		}
	}

	assertScalar(t, pool,
		"SELECT toUInt64(sum(reading_count)) AS v FROM "+rollupTable+
			" WHERE tenant_id = '"+probeTenant+"'",
		1, "reading_count after the same insert twice")
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+"'",
		1, "raw rows after the same insert twice")
}

func assertScalar(t *testing.T, pool *chpool.Pool, query string, want uint64, what string) {
	t.Helper()
	var got proto.ColUInt64
	if err := pool.Do(context.Background(), ch.Query{
		Body:   query,
		Result: proto.Results{{Name: "v", Data: &got}},
	}); err != nil {
		t.Fatalf("%s: %v", what, err)
	}
	if got.Rows() != 1 || got.Row(0) != want {
		t.Errorf("%s = %v, want %d", what, got, want)
	}
}

func deleteProbeRows(t *testing.T, pool *chpool.Pool) {
	t.Helper()
	for _, table := range []string{readingsTable, rollupTable} {
		err := pool.Do(context.Background(), ch.Query{
			Body:     "ALTER TABLE " + table + " DELETE WHERE tenant_id = '" + probeTenant + "'",
			Settings: []ch.Setting{{Key: "mutations_sync", Value: "2"}},
		})
		if err != nil {
			t.Errorf("cleaning %s: %v", table, err)
		}
	}
}
