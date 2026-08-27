// Package lag measures how long an accepted span takes to become queryable.
//
// It follows a sample of real load traces rather than injecting probes of its
// own, so what it times is the pipeline the load is already driving: receiver,
// broker, worker batch, ClickHouse insert, and the part visible to a reader.
//
// The figure is therefore accurate to one poll interval and never better than
// that. It is reported alongside the numbers so nobody reads more precision
// into them than was measured.
package lag

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

// pendingLimit caps how many traces are followed at once. The sample interval
// already bounds the rate; this bounds the query if storage falls far behind.
const pendingLimit = 2000

// Tracker follows accepted traces until they can be read back.
type Tracker struct {
	store       *store
	serviceName string
	poll        time.Duration

	mu       sync.Mutex
	pending  map[string]time.Time
	resolved []time.Duration
	lost     int
	failure  error
}

// Options are what the tracker needs to find its traces. All are required.
type Options struct {
	URL      string
	User     string
	Password string
	Database string

	// ServiceName narrows every query to this run's own spans.
	ServiceName string
	// PollInterval is both how often storage is asked and the resolution of
	// every duration reported.
	PollInterval time.Duration
}

// NewTracker returns a tracker querying the ClickHouse HTTP interface.
func NewTracker(opts Options) *Tracker {
	return &Tracker{
		store: &store{
			endpoint: opts.URL,
			user:     opts.User,
			password: opts.Password,
			database: opts.Database,
			client:   &http.Client{Timeout: 10 * time.Second},
		},
		serviceName: opts.ServiceName,
		poll:        opts.PollInterval,
		pending:     make(map[string]time.Time),
	}
}

// Follow starts watching for traceID. It satisfies measure.TraceSink.
func (t *Tracker) Follow(traceID string, acceptedAt time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()

	if len(t.pending) >= pendingLimit {
		return
	}
	if _, already := t.pending[traceID]; already {
		return
	}
	t.pending[traceID] = acceptedAt
}

// Run polls storage until ctx is cancelled or nothing is left outstanding.
func (t *Tracker) Run(ctx context.Context) {
	ticker := time.NewTicker(t.poll)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			t.giveUpOnPending()
			return
		case <-ticker.C:
			t.resolveVisible(ctx)
		}
	}
}

func (t *Tracker) resolveVisible(ctx context.Context) {
	outstanding := t.outstanding()
	if len(outstanding) == 0 {
		return
	}

	visible, err := t.store.visibleTraces(ctx, t.serviceName, outstanding)
	if err != nil {
		// One failed poll is not fatal — the next one may succeed — but the
		// first cause is kept so the report can say why nothing resolved.
		t.recordFailure(err)
		return
	}

	seenAt := time.Now()
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, traceID := range visible {
		acceptedAt, waiting := t.pending[traceID]
		if !waiting {
			continue
		}
		delete(t.pending, traceID)
		t.resolved = append(t.resolved, seenAt.Sub(acceptedAt))
	}
}

func (t *Tracker) outstanding() []string {
	t.mu.Lock()
	defer t.mu.Unlock()

	ids := make([]string, 0, len(t.pending))
	for traceID := range t.pending {
		ids = append(ids, traceID)
	}
	return ids
}

func (t *Tracker) recordFailure(err error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.failure == nil {
		t.failure = err
	}
}

// giveUpOnPending counts whatever never arrived. A trace still outstanding when
// the drain window closes is a lost measurement, not a lag of zero.
func (t *Tracker) giveUpOnPending() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.lost += len(t.pending)
	t.pending = make(map[string]time.Time)
}

// Result is the lag distribution over the traces that became queryable.
type Result struct {
	Samples int
	// Lost is how many followed traces never appeared before the drain window
	// closed. Any value above zero makes the percentiles a lower bound.
	Lost int
	P50  time.Duration
	P90  time.Duration
	P99  time.Duration
	Max  time.Duration
	// Failure is the first polling error, if storage could not be reached.
	Failure error
}

// Result summarises what became queryable. Call it once Run has returned.
func (t *Tracker) Result() Result {
	t.mu.Lock()
	defer t.mu.Unlock()

	result := Result{Samples: len(t.resolved), Lost: t.lost, Failure: t.failure}
	if len(t.resolved) == 0 {
		return result
	}

	sorted := make([]time.Duration, len(t.resolved))
	copy(sorted, t.resolved)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })

	result.P50 = percentile(sorted, 0.50)
	result.P90 = percentile(sorted, 0.90)
	result.P99 = percentile(sorted, 0.99)
	result.Max = sorted[len(sorted)-1]
	return result
}

func percentile(sorted []time.Duration, share float64) time.Duration {
	rank := int(share*float64(len(sorted)) + 0.5)
	if rank < 1 {
		rank = 1
	}
	if rank > len(sorted) {
		rank = len(sorted)
	}
	return sorted[rank-1]
}

// store is the ClickHouse HTTP interface, used directly rather than through a
// driver: one read-only query needs no connection pooling or type mapping.
type store struct {
	endpoint string
	user     string
	password string
	database string
	client   *http.Client
}

// visibleTraces returns which of traceIDs can now be read back.
func (s *store) visibleTraces(ctx context.Context, serviceName string, traceIDs []string) ([]string, error) {
	query := fmt.Sprintf(
		"SELECT DISTINCT trace_id FROM spans WHERE service_name = %s AND trace_id IN (%s)",
		quote(serviceName), quoteAll(traceIDs))

	address, err := url.Parse(s.endpoint)
	if err != nil {
		return nil, fmt.Errorf("clickhouse url: %w", err)
	}
	address.RawQuery = url.Values{"database": {s.database}}.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, address.String(), strings.NewReader(query))
	if err != nil {
		return nil, err
	}
	request.SetBasicAuth(s.user, s.password)

	response, err := s.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("querying clickhouse: %w", err)
	}
	defer func() { _ = response.Body.Close() }()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, fmt.Errorf("reading clickhouse response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("clickhouse answered %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	return strings.Fields(string(body)), nil
}

// quote renders a SQL string literal. Every value reaching it is either a
// configured service name or a hex trace id we encoded ourselves, but escaping
// is done anyway so that stays true of future callers.
func quote(value string) string {
	return "'" + strings.NewReplacer("\\", "\\\\", "'", "\\'").Replace(value) + "'"
}

func quoteAll(values []string) string {
	quoted := make([]string, len(values))
	for i, value := range values {
		quoted[i] = quote(value)
	}
	return strings.Join(quoted, ",")
}
