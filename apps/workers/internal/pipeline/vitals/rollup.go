package vitals

import (
	"sort"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/latencysample"
)

// Stat is one row of `web_vitals_1m`: what one vital did, on one page, for one
// device class, in one minute.
type Stat struct {
	Minute               time.Time
	TenantID             string
	ProjectID            string
	DerivedRetentionDays uint16
	VitalName            string
	Route                string
	URLPath              string
	DeviceClass          string
	Country              string
	ReadingCount         uint64
	// Values are the readings behind this row, sorted ascending and capped. The
	// store folds them into the row's quantile state: no client builds an
	// AggregateFunction, so its serialised form stays the server's.
	Values []float64
}

// statKey identifies one row of `web_vitals_1m`. It is the table's own sort key,
// so a bucket and a row are the same thing, and it is a comparable struct so it
// can key a map without formatting a string per reading.
type statKey struct {
	minute      time.Time
	tenantID    string
	projectID   string
	vitalName   string
	route       string
	urlPath     string
	deviceClass string
	country     string
	// Constant for a given project, but carried because it sits in the target
	// table's partition key.
	derivedRetentionDays uint16
}

// bucket is what one key accumulates before it becomes a row.
type bucket struct {
	readings  uint64
	reservoir latencysample.Reservoir
}

// Rollup folds readings into per-minute buckets.
//
// It holds only what the current batch produced. Unlike the trace pipeline's
// counters there is no cross-batch accumulation to reconcile: a vital is a
// complete measurement on arrival, so a minute that spans two batches simply
// becomes two rows that AggregatingMergeTree merges — which is what the engine
// is for.
type Rollup struct {
	buckets map[statKey]*bucket
}

// NewRollup returns an empty rollup ready to fold readings into.
func NewRollup() *Rollup { return &Rollup{buckets: make(map[statKey]*bucket)} }

// Add folds one reading into its minute.
//
// The minute comes from `received_at`, not from the browser's timestamp. A
// visitor's device clock can be set to any year at all, and a rollup
// bucketed on it would scatter one page's readings across decades of partitions
// — where the raw table merely stores the wrong `timestamp` beside a correct
// `received_at`.
func (r *Rollup) Add(reading Reading) {
	key := statKey{
		minute:               reading.ReceivedAt.UTC().Truncate(time.Minute),
		tenantID:             reading.TenantID,
		projectID:            reading.ProjectID,
		vitalName:            reading.VitalName,
		route:                reading.Route,
		urlPath:              reading.URLPath,
		deviceClass:          reading.DeviceClass,
		country:              reading.Country,
		derivedRetentionDays: reading.DerivedRetentionDays,
	}
	held, seen := r.buckets[key]
	if !seen {
		held = &bucket{}
		r.buckets[key] = held
	}
	held.readings++
	held.reservoir.Observe(latencysample.WeighIdentity([]byte(reading.VitalID)), reading.Value)
}

// Rows returns what the rollup holds, ordered so the same readings always
// produce the same block.
//
// Sorted for the reason the endpoint rollup's rows are: the block ClickHouse
// receives must be byte-identical when a restart re-derives the same minute
// from the same offsets, or the insert deduplication token cannot recognise the
// retry and the rollup double-counts.
func (r *Rollup) Rows() []Stat {
	rows := make([]Stat, 0, len(r.buckets))
	for key, held := range r.buckets {
		rows = append(rows, Stat{
			Minute:               key.minute,
			TenantID:             key.tenantID,
			ProjectID:            key.projectID,
			DerivedRetentionDays: key.derivedRetentionDays,
			VitalName:            key.vitalName,
			Route:                key.route,
			URLPath:              key.urlPath,
			DeviceClass:          key.deviceClass,
			Country:              key.country,
			ReadingCount:         held.readings,
			Values:               held.reservoir.Values(),
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].less(rows[j]) })
	return rows
}

// Reset empties the rollup, keeping the map's capacity for the next batch.
func (r *Rollup) Reset() { clear(r.buckets) }

// less orders rollup rows totally, so a drain is reproducible. The fields are
// the whole key: any pair left tied would order at the map's whim.
func (s Stat) less(other Stat) bool {
	switch {
	case !s.Minute.Equal(other.Minute):
		return s.Minute.Before(other.Minute)
	case s.TenantID != other.TenantID:
		return s.TenantID < other.TenantID
	case s.ProjectID != other.ProjectID:
		return s.ProjectID < other.ProjectID
	case s.VitalName != other.VitalName:
		return s.VitalName < other.VitalName
	case s.Route != other.Route:
		return s.Route < other.Route
	case s.URLPath != other.URLPath:
		return s.URLPath < other.URLPath
	case s.DeviceClass != other.DeviceClass:
		return s.DeviceClass < other.DeviceClass
	case s.Country != other.Country:
		return s.Country < other.Country
	default:
		return s.DerivedRetentionDays < other.DerivedRetentionDays
	}
}
