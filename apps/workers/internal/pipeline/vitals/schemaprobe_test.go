//go:build schemaprobe

package vitals

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// probeTenantPrefix marks every row any run of this probe writes, so a run that
// died before its cleanup does not leave rows behind forever.
const probeTenantPrefix = "__m5_vitals_probe__"

// probeTenant is this run's own tenant, and it is per-run because the rollup's
// deduplication token is derived from the target's sort key — tenant included.
// Fixture rows that were identical between runs produced an identical token
// inside one wall-clock minute, and the store dropped the second insert without
// an error, leaving every rollup assertion reading zero.
var probeTenant = fmt.Sprintf("%s%d-%d", probeTenantPrefix, os.Getpid(), time.Now().UnixNano())

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
	assertInsertLanded(t, pool, readingsTable, rollupTable)

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
	// The second insert is meant to store nothing; the first is not.
	assertInsertLanded(t, pool, readingsTable, rollupTable)

	assertScalar(t, pool,
		"SELECT toUInt64(sum(reading_count)) AS v FROM "+rollupTable+
			" WHERE tenant_id = '"+probeTenant+"'",
		1, "reading_count after the same insert twice")
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+"'",
		1, "raw rows after the same insert twice")
}

// TestTheRouteLandsInItsColumnAndGroupsThePages sends a customer with both kinds
// of page through the real writer: three order URLs under one route template,
// and one page that configured none. It then reads them back the way the API's
// page breakdown does — `if(route != ”, route, url_path)`.
//
// This is the whole change end to end. A unit test can prove the attribute is
// read; only the server can prove the column exists, that the rollup's new sort
// key accepts the row, and that three quantile states written under three
// different `url_path` values merge into one page's distribution.
func TestTheRouteLandsInItsColumnAndGroupsThePages(t *testing.T) {
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

	const template = "/cart/checkout/{orderId}"
	now := time.Now().UTC()
	rollup := NewRollup()
	readings := make([]Reading, 0, 4)

	// Three orders under one template. 100, 200, 300 → p75 250.
	for i, value := range []float64{100, 200, 300} {
		reading := Reading{
			Timestamp: now, ReceivedAt: now, RetentionDays: 30, DerivedRetentionDays: 395,
			TenantID: probeTenant, ProjectID: "probe-project", SessionID: "probe-session",
			VitalName: "lcp", VitalID: string(rune('p' + i)), Value: value, Delta: value,
			Rating: "good", NavigationType: "navigate",
			URLPath: "/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab72" + string(rune('0'+i)),
			Route:   template, DeviceClass: DeviceClassDesktop, Attrs: "{}",
		}
		readings = append(readings, reading)
		rollup.Add(reading)
	}
	// One page the customer configured no template for. It must still appear.
	unrouted := Reading{
		Timestamp: now, ReceivedAt: now, RetentionDays: 30, DerivedRetentionDays: 395,
		TenantID: probeTenant, ProjectID: "probe-project", SessionID: "probe-session",
		VitalName: "lcp", VitalID: "u", Value: 900, Delta: 900,
		Rating: "poor", NavigationType: "navigate",
		URLPath: "/about", DeviceClass: DeviceClassDesktop, Attrs: "{}",
	}
	readings = append(readings, unrouted)
	rollup.Add(unrouted)

	if err := NewWriter(pool).Insert(ctx, readings, rollup.Rows()); err != nil {
		t.Fatalf("the server refused the writer's blocks: %v", err)
	}
	assertInsertLanded(t, pool, readingsTable, rollupTable)

	// The raw column holds the template the browser sent, not a derived one.
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+
			"' AND route = '"+template+"'",
		3, "raw readings carrying the route")
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+readingsTable+" WHERE tenant_id = '"+probeTenant+
			"' AND route = '' AND url_path = '/about'",
		1, "the unrouted reading, with an empty route rather than a guess")

	// The rollup still holds a row per URL — url_path is in the key — and the
	// read is what collapses them into one page.
	assertScalar(t, pool,
		"SELECT toUInt64(count()) AS v FROM "+rollupTable+" WHERE tenant_id = '"+probeTenant+"'",
		4, "rollup rows before the read groups them")
	assertScalar(t, pool,
		"SELECT toUInt64(uniqExact(if(route != '', route, url_path))) AS v FROM "+rollupTable+
			" WHERE tenant_id = '"+probeTenant+"'",
		2, "pages the breakdown groups those rows into")

	// The three orders' quantile states merge, which is the point: 250 is the
	// p75 of 100, 200 and 300 together, not of any one row.
	assertScalar(t, pool,
		"SELECT toUInt64(quantilesMerge(0.5, 0.75, 0.95)(value_quantiles)[2]) AS v FROM "+
			rollupTable+" WHERE tenant_id = '"+probeTenant+"' AND route = '"+template+"'",
		250, "p75 across the three orders under one template")
	// The unrouted page keeps its own distribution rather than joining theirs.
	assertScalar(t, pool,
		"SELECT toUInt64(quantilesMerge(0.5, 0.75, 0.95)(value_quantiles)[2]) AS v FROM "+
			rollupTable+" WHERE tenant_id = '"+probeTenant+"' AND route = '' AND url_path = '/about'",
		900, "p75 of the page with no route template")
}

// assertInsertLanded proves the insert was stored before anything asks what
// shape it has. A deduplicated insert is accepted and dropped, so it reads back
// as zero rows — and `quantilesMerge` over none of them raises a nan conversion
// error that points nowhere near the cause.
func assertInsertLanded(t *testing.T, pool *chpool.Pool, tables ...string) {
	t.Helper()
	for _, table := range tables {
		stored := scalar(t, pool,
			"SELECT toUInt64(count()) AS v FROM "+table+" WHERE tenant_id = '"+probeTenant+"'",
			"rows stored in "+table)
		if stored == 0 {
			t.Fatalf("%s holds no rows for %s: the insert was accepted and stored nothing, "+
				"which is what a repeated insert_deduplication_token looks like",
				table, probeTenant)
		}
		t.Logf("%s stored %d rows for this run", table, stored)
	}
}

func assertScalar(t *testing.T, pool *chpool.Pool, query string, want uint64, what string) {
	t.Helper()
	// Zero is what a dropped insert reads back as, so it can never be the
	// expectation: prove the rows landed, then assert on the subset.
	if want == 0 {
		t.Fatalf("%s expects 0, which an insert that stored nothing also satisfies", what)
	}
	if got := scalar(t, pool, query, what); got != want {
		t.Errorf("%s = %d, want %d", what, got, want)
	}
}

func scalar(t *testing.T, pool *chpool.Pool, query, what string) uint64 {
	t.Helper()
	var got proto.ColUInt64
	if err := pool.Do(context.Background(), ch.Query{
		Body:   query,
		Result: proto.Results{{Name: "v", Data: &got}},
	}); err != nil {
		t.Fatalf("%s: %v", what, err)
	}
	if got.Rows() != 1 {
		t.Fatalf("%s returned %d rows, want 1", what, got.Rows())
	}
	return got.Row(0)
}

// deleteProbeRows removes every probe run's rows, not only this one's: the
// tenant is per-run, so a run killed before its cleanup would otherwise leave
// rows nothing ever sweeps but the table's TTL.
func deleteProbeRows(t *testing.T, pool *chpool.Pool) {
	t.Helper()
	for _, table := range []string{readingsTable, rollupTable} {
		err := pool.Do(context.Background(), ch.Query{
			Body: "ALTER TABLE " + table +
				" DELETE WHERE startsWith(tenant_id, '" + probeTenantPrefix + "')",
			Settings: []ch.Setting{{Key: "mutations_sync", Value: "2"}},
		})
		if err != nil {
			t.Errorf("cleaning %s: %v", table, err)
		}
	}
}
