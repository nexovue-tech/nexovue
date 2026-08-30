package projectconfig

import (
	"context"
	"log/slog"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// Row is one project's stored configuration, as JSON documents. Parsing happens
// here rather than in the reader so a store that is not Postgres needs to
// understand nothing about the rules.
type Row struct {
	ProjectID      string
	MaskingConfig  []byte
	SamplingConfig []byte
	// Days this project's raw signals and derived rollups are kept for. Stamped
	// on every row written, so a whole part shares one value and can expire as
	// a unit rather than being rewritten row by row (ADR 0072).
	RawRetentionDays     uint16
	DerivedRetentionDays uint16

	// FirstTracesStored is how many traces this project's first-traces carve-out
	// has kept, summed over every worker that has ever reported one.
	FirstTracesStored int64
}

// Store is the control plane as the refresher uses it: every project's stored
// configuration, and the running count of the traces each project's first-traces
// carve-out has kept. An interface because the refresher's behaviour — what it
// does with a malformed row, what it does when the store is unreachable — is the
// part worth testing, and none of it needs a database.
//
// Reading and writing are one interface because they are one round trip each,
// taken by the same goroutine on the same tick.
type Store interface {
	ReadProjects(ctx context.Context) ([]Row, error)

	// AddFirstTracesStored adds each project's newly stored traces to the count
	// already recorded. Adding, not setting: every worker counts its own.
	AddFirstTracesStored(ctx context.Context, stored map[string]int64) error
}

// Refresher keeps a Cache in step with the store.
type Refresher struct {
	store    Store
	cache    *Cache
	defaults Settings
	interval time.Duration
	log      *slog.Logger

	// The deployment-wide masking secret, from which each project's hashing key
	// is derived. Empty means hashing is unavailable and a rule asking for it is
	// rejected rather than weakened.
	maskSecret []byte
}

// NewRefresher returns a refresher that replaces cache's snapshot every
// interval.
func NewRefresher(
	store Store,
	cache *Cache,
	defaults Settings,
	interval time.Duration,
	log *slog.Logger,
	maskSecret []byte,
) *Refresher {
	return &Refresher{
		store:      store,
		cache:      cache,
		defaults:   defaults,
		interval:   interval,
		maskSecret: maskSecret,
		log:        log.With(slog.String("component", "project-config")),
	}
}

// Refresh writes what this process stored under the first-traces carve-out,
// then loads every project and installs the result.
//
// The write comes first so the read that follows it reports totals this
// worker's own traces are already in; the other way round, every worker would
// spend a full interval believing a carve-out was emptier than it is.
//
// A failure leaves the previous snapshot in place. That is the safe direction:
// the alternative — emptying the cache when the control plane is unreachable —
// would silently turn a project's extra masking rules off during an outage.
func (r *Refresher) Refresh(ctx context.Context) error {
	r.recordFirstTraces(ctx)

	rows, err := r.store.ReadProjects(ctx)
	if err != nil {
		return err
	}

	projects := make(map[string]Settings, len(rows))
	for _, row := range rows {
		projects[row.ProjectID] = r.parse(ctx, row)
	}
	r.cache.Replace(projects)
	return nil
}

// parse turns one row into settings, falling back to the defaults for whichever
// half is malformed.
//
// The comment here used to claim the defaults mask more, so a typo could never
// quietly reduce masking. That is false: the defaults name no keys at all, so a
// rejected document silently stops masking every custom key the project asked
// for — the failure mode is the opposite of the one described. Sampling is
// genuinely safe to default; masking is not, so a rejected masking document now
// keeps whatever last parsed for that project and only falls back to the
// defaults when nothing ever has.
func (r *Refresher) parse(ctx context.Context, row Row) Settings {
	settings := r.defaults

	rules, err := mask.RulesFromJSON(row.MaskingConfig, r.maskOptionsFor(row.ProjectID)...)
	switch previous, known := r.cache.Loaded(row.ProjectID); {
	case err == nil:
		settings.Masking = rules
	case known:
		settings.Masking = previous.Masking
		r.log.ErrorContext(ctx, "project masking config rejected, keeping the last good one",
			slog.String("project_id", row.ProjectID), slog.String("error", err.Error()))
	default:
		r.log.ErrorContext(ctx, "project masking config rejected and none was ever loaded; "+
			"falling back to defaults, which name no custom keys",
			slog.String("project_id", row.ProjectID), slog.String("error", err.Error()))
	}

	policy, err := sample.PolicyFromJSON(row.SamplingConfig, r.defaults.Sampling)
	if err != nil {
		r.log.ErrorContext(ctx, "project sampling config ignored, using defaults",
			slog.String("project_id", row.ProjectID), slog.String("error", err.Error()))
	} else {
		settings.Sampling = policy
	}

	// Retention is a number the control plane resolved, not a document this
	// worker parses, so there is nothing here that can be malformed. Zero means
	// the view had no row and the deployment default stands.
	if row.RawRetentionDays > 0 {
		settings.RawRetentionDays = row.RawRetentionDays
	}
	if row.DerivedRetentionDays > 0 {
		settings.DerivedRetentionDays = row.DerivedRetentionDays
	}
	// Zero is a real value here, not an absent one: it says this project has
	// stored nothing yet, which is what leaves its carve-out open.
	settings.FirstTracesStored = row.FirstTracesStored
	return settings
}

// recordFirstTraces writes what the carve-out has kept in this process since the
// last refresh.
//
// A write that fails is not a refresh that fails: the counts go back into the
// cache and the only consequence is a carve-out that stays open a little
// longer, which is the direction that cannot lose a customer's first span.
func (r *Refresher) recordFirstTraces(ctx context.Context) {
	stored := r.cache.takeStoredTraces()
	if len(stored) == 0 {
		return
	}
	if err := r.store.AddFirstTracesStored(ctx, stored); err != nil {
		r.cache.returnStoredTraces(stored)
		if ctx.Err() != nil {
			return
		}
		r.log.WarnContext(ctx, "could not record first-traces progress, keeping it for the next refresh",
			slog.Int("projects", len(stored)), slog.String("error", err.Error()))
		return
	}
	r.log.DebugContext(ctx, "recorded first-traces progress",
		slog.Int("projects", len(stored)))
}

// Run refreshes on a timer until ctx is cancelled. The first load happens
// immediately, so a worker is never more than one interval behind the control
// plane and is correct from its first span if Postgres is up.
func (r *Refresher) Run(ctx context.Context) {
	r.refreshAndLog(ctx)

	ticker := time.NewTicker(r.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.refreshAndLog(ctx)
		}
	}
}

func (r *Refresher) refreshAndLog(ctx context.Context) {
	if err := r.Refresh(ctx); err != nil {
		if ctx.Err() != nil {
			return
		}
		// Warn, not error: the previous snapshot is still serving, so this is
		// degraded rather than broken.
		r.log.WarnContext(ctx, "project config refresh failed, keeping previous snapshot",
			slog.String("error", err.Error()))
		return
	}
	r.log.DebugContext(ctx, "project config refreshed",
		slog.Int("projects", r.cache.Configured()))
}

// maskOptionsFor derives this project's hashing key from the deployment secret.
func (r *Refresher) maskOptionsFor(projectID string) []mask.Option {
	if len(r.maskSecret) == 0 {
		return nil
	}
	key, err := mask.DeriveHashKey(r.maskSecret, projectID)
	if err != nil {
		// Width is validated at boot, so this cannot fire without the config
		// layer being wrong.
		return nil
	}
	return []mask.Option{mask.WithHashKey(key)}
}
