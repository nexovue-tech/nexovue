package projectconfig_test

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"maps"
	"reflect"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/projectconfig"
)

// fakeStore answers with whatever the test set, can be told to fail, and keeps
// the first-traces counts it was given the way the control plane does: by
// adding them to what is already there.
type fakeStore struct {
	rows []projectconfig.Row
	err  error

	// recorded is every flush it accepted, in order, so a test can see what the
	// refresher wrote rather than only what it ended up with.
	recorded []map[string]int64
	writeErr error
}

func (f *fakeStore) ReadProjects(context.Context) ([]projectconfig.Row, error) {
	return f.rows, f.err
}

func (f *fakeStore) AddFirstTracesStored(_ context.Context, stored map[string]int64) error {
	if f.writeErr != nil {
		return f.writeErr
	}
	f.recorded = append(f.recorded, maps.Clone(stored))
	for projectID, traces := range stored {
		f.addStored(projectID, traces)
	}
	return nil
}

// addStored is the store's own accumulation: the count a later read reports is
// the sum of every worker's flushes.
func (f *fakeStore) addStored(projectID string, traces int64) {
	for i := range f.rows {
		if f.rows[i].ProjectID == projectID {
			f.rows[i].FirstTracesStored += traces
			return
		}
	}
	f.rows = append(f.rows, projectconfig.Row{ProjectID: projectID, FirstTracesStored: traces})
}

// carveOutOff is the first-traces size these tests pass when the carve-out is
// not what they are about. Zero is off, so the policy they get back is the
// stored one and nothing else.
const carveOutOff = 0

func defaults() projectconfig.Settings {
	return projectconfig.Settings{
		Masking:  mask.Defaults(),
		Sampling: sample.DefaultPolicy(2*time.Second, 10),
	}
}

func quietLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func newRefresher(store projectconfig.Store, cache *projectconfig.Cache) *projectconfig.Refresher {
	return projectconfig.NewRefresher(store, cache, defaults(), time.Minute, quietLogger(), nil)
}

func TestAProjectWithoutARowGetsTheDefaults(t *testing.T) {
	cache := projectconfig.NewCache(defaults())

	if got := cache.SamplingPolicy("unknown-project", carveOutOff); got != defaults().Sampling {
		t.Errorf("SamplingPolicy() = %+v, want the deployment default %+v", got, defaults().Sampling)
	}
}

func TestARefreshInstallsPerProjectSettings(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	reader := &fakeStore{rows: []projectconfig.Row{{
		ProjectID:      "project-a",
		MaskingConfig:  []byte(`{"mask_keys":["user.email"]}`),
		SamplingConfig: []byte(`{"healthy_percent":50}`),
	}}}

	if err := newRefresher(reader, cache).Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	if got := cache.SamplingPolicy("project-a", carveOutOff).HealthyPercent; got != 50 {
		t.Errorf("HealthyPercent for project-a = %d, want 50", got)
	}
	if got := cache.SamplingPolicy("project-b", carveOutOff).HealthyPercent; got != 10 {
		t.Errorf("HealthyPercent for an unconfigured project = %d, want the default 10", got)
	}
}

// A rule change has to take effect without restarting a worker, and removing a
// row has to restore default behaviour rather than leaving stale rules running.
func TestASecondRefreshReplacesTheSnapshotWholesale(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	reader := &fakeStore{rows: []projectconfig.Row{{
		ProjectID: "project-a", SamplingConfig: []byte(`{"healthy_percent":50}`),
	}}}
	refresher := newRefresher(reader, cache)

	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	reader.rows = nil
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}

	if got := cache.SamplingPolicy("project-a", carveOutOff).HealthyPercent; got != 10 {
		t.Errorf("HealthyPercent = %d after the row was removed, want the default 10", got)
	}
	if cache.Configured() != 0 {
		t.Errorf("Configured() = %d, want 0", cache.Configured())
	}
}

// Emptying the cache when the control plane is unreachable would silently turn
// a project's extra masking rules off during an outage.
func TestAFailedRefreshKeepsThePreviousSnapshot(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	reader := &fakeStore{rows: []projectconfig.Row{{
		ProjectID: "project-a", SamplingConfig: []byte(`{"healthy_percent":50}`),
	}}}
	refresher := newRefresher(reader, cache)

	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	reader.err = errors.New("postgres unreachable")
	if err := refresher.Refresh(context.Background()); err == nil {
		t.Fatal("Refresh() succeeded while the store was down, want the failure reported")
	}

	if got := cache.SamplingPolicy("project-a", carveOutOff).HealthyPercent; got != 50 {
		t.Errorf("HealthyPercent = %d after a failed refresh, want the previous 50", got)
	}
}

// A typo in one project's document must not take masking down for everyone,
// and must not quietly reduce that project's masking either.
func TestAMalformedDocumentFallsBackToTheDefaultsForThatProjectOnly(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	reader := &fakeStore{rows: []projectconfig.Row{
		{ProjectID: "broken", MaskingConfig: []byte(`{"disable":["everything"]}`),
			SamplingConfig: []byte(`{"healthy_percent":"most"}`)},
		{ProjectID: "fine", SamplingConfig: []byte(`{"healthy_percent":25}`)},
	}}

	if err := newRefresher(reader, cache).Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	if got := cache.SamplingPolicy("broken", carveOutOff); got != defaults().Sampling {
		t.Errorf("SamplingPolicy(broken) = %+v, want the defaults %+v", got, defaults().Sampling)
	}
	if got := cache.SamplingPolicy("fine", carveOutOff).HealthyPercent; got != 25 {
		t.Errorf("HealthyPercent(fine) = %d, want 25: one bad row must not affect another", got)
	}
	// The defaults mask more than any configuration can, which is the safe
	// direction for a document nobody could parse.
	if !reflect.DeepEqual(cache.MaskingRules("broken"), defaults().Masking) {
		t.Error("MaskingRules(broken) is not the defaults; a bad document must never mask less")
	}
}
