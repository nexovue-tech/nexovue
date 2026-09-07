package vitals

import (
	"fmt"
	"math/rand"
	"reflect"
	"testing"
	"time"
)

func readingAt(second int, vital, path, id string, value float64) Reading {
	return Reading{
		ReceivedAt:           time.Date(2026, 9, 6, 11, 30, second, 0, time.UTC),
		DerivedRetentionDays: 395,
		TenantID:             "tenant-a",
		ProjectID:            "project-a",
		VitalName:            vital,
		VitalID:              id,
		Value:                value,
		URLPath:              path,
		DeviceClass:          DeviceClassMobile,
	}
}

func TestReadingsInOneMinuteBecomeOneRow(t *testing.T) {
	rollup := NewRollup()
	rollup.Add(readingAt(1, "lcp", "/checkout", "a", 1000))
	rollup.Add(readingAt(59, "lcp", "/checkout", "b", 2000))

	rows := rollup.Rows()
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1: both readings fall in the same minute", len(rows))
	}
	if rows[0].ReadingCount != 2 {
		t.Errorf("reading_count = %d, want 2", rows[0].ReadingCount)
	}
	if !reflect.DeepEqual(rows[0].Values, []float64{1000, 2000}) {
		t.Errorf("values = %v, want [1000 2000] ascending", rows[0].Values)
	}
	if rows[0].Minute.Second() != 0 {
		t.Errorf("minute = %v, want it truncated to the minute", rows[0].Minute)
	}
}

func TestEachDimensionOfTheSortKeySplitsTheBucket(t *testing.T) {
	rollup := NewRollup()
	base := readingAt(1, "lcp", "/checkout", "a", 1000)

	byVital := base
	byVital.VitalName = "inp"
	byPath := base
	byPath.URLPath = "/cart"
	byRoute := base
	byRoute.Route = "/cart/{id}"
	byDevice := base
	byDevice.DeviceClass = DeviceClassDesktop
	byCountry := base
	byCountry.Country = "IN"

	for _, reading := range []Reading{base, byVital, byPath, byRoute, byDevice, byCountry} {
		rollup.Add(reading)
	}
	if rows := rollup.Rows(); len(rows) != 6 {
		t.Fatalf("rows = %d, want 6: every dimension in the key splits the bucket", len(rows))
	}
}

// Two order ids under one template are two rollup rows — `url_path` is still in
// the key — and the store merges their quantile states at read time. What the
// rollup must not do is lose the template, which is the only thing that lets
// the read collapse them.
func TestTheRouteTravelsOntoEveryRowItGrouped(t *testing.T) {
	const template = "/cart/checkout/{orderId}"
	rollup := NewRollup()
	for _, id := range []string{"978ac453", "aa9f11f1"} {
		reading := readingAt(1, "lcp", "/cart/checkout/"+id, id, 1000)
		reading.Route = template
		rollup.Add(reading)
	}

	rows := rollup.Rows()
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2: url_path is still a dimension of the key", len(rows))
	}
	for _, row := range rows {
		if row.Route != template {
			t.Errorf("route = %q on the row for %q, want %q", row.Route, row.URLPath, template)
		}
	}
}

// A minute re-derived from the same offsets has to produce the same block, or
// the insert deduplication token cannot recognise the retry and the rollup
// double-counts. Arrival order is the part a replay does not promise to repeat.
func TestTheSameReadingsProduceTheSameRowsInAnyOrder(t *testing.T) {
	readings := make([]Reading, 0, 200)
	for i := range 200 {
		readings = append(readings, readingAt(
			i%60,
			[]string{"lcp", "inp", "cls"}[i%3],
			fmt.Sprintf("/page-%d", i%7),
			fmt.Sprintf("id-%d", i),
			float64(i)*1.5,
		))
	}

	first := NewRollup()
	for _, reading := range readings {
		first.Add(reading)
	}
	want := first.Rows()

	shuffled := append([]Reading(nil), readings...)
	rand.New(rand.NewSource(7)).Shuffle(len(shuffled), func(i, j int) {
		shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
	})

	second := NewRollup()
	for _, reading := range shuffled {
		second.Add(reading)
	}

	if got := second.Rows(); !reflect.DeepEqual(got, want) {
		t.Fatalf("the same readings in another order produced different rows:\n got %d rows\nwant %d rows",
			len(got), len(want))
	}
}

func TestResetLeavesNothingBehind(t *testing.T) {
	rollup := NewRollup()
	rollup.Add(readingAt(1, "lcp", "/checkout", "a", 1000))
	rollup.Reset()

	if rows := rollup.Rows(); len(rows) != 0 {
		t.Fatalf("rows = %d after reset, want 0: the next chunk would re-insert them", len(rows))
	}
}
