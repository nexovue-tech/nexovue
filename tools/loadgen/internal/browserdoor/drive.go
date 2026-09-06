package browserdoor

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// The browser door's own path and header, spelled as apps/receiver names them.
// The key may also travel in the query string — that is the unload beacon's
// path, which needs no preflight — but a page that can set headers sends the
// header, and that is the path a load test should measure.
const (
	logsPath      = "/browser/v1/logs"
	siteKeyHeader = "x-nexovue-key"
)

// Options are what one browser-door run needs.
type Options struct {
	// Endpoint is the receiver's base URL, without the signal path. Point it at
	// the measuring proxy to get the door's latency into the report.
	Endpoint string
	// SiteKey is public by design; it is checked against Origin, not kept secret.
	SiteKey string
	// Origin must be one the site key allows, or the door refuses everything and
	// the run measures rejection.
	Origin      string
	ServiceName string
	// Release is stamped as service.version, which is what a source map is
	// matched on. Without it a stack on the Errors screen cannot symbolicate.
	Release string
	// Visitors is how many simulated browsers run at once, each keeping its own
	// session id.
	Visitors int
	// PageLoadsPerSecond is the offered load across every visitor. Zero sends as
	// fast as the door accepts.
	PageLoadsPerSecond float64
	// ErrorRate is the share of page loads that also throw. 0.15 puts enough on
	// the Errors screen to group without drowning the vitals.
	ErrorRate float64
	Duration  time.Duration
	Timeout   time.Duration
}

// Result is what the run observed.
type Result struct {
	PageLoads int64
	Vitals    int64
	Errors    int64
	// Accepted and Refused count exports, not records. A refusal is a result —
	// most often an origin the site key does not allow — so it is reported
	// rather than treated as a reason to stop.
	Accepted int64
	Refused  int64
	// FirstRefusal is what the door said the first time it said no, because a
	// run of nothing but refusals is otherwise a silent zero.
	FirstRefusal string
}

// Drive sends browser telemetry until the duration elapses or ctx is cancelled.
func Drive(ctx context.Context, opts Options, log *slog.Logger) (Result, error) {
	if opts.SiteKey == "" || opts.Origin == "" {
		return Result{}, fmt.Errorf("browser door needs a site key and an allowed origin")
	}

	runCtx, stop := context.WithTimeout(ctx, opts.Duration)
	defer stop()

	client := &http.Client{Timeout: opts.Timeout}
	tally := &counters{}
	interval := perVisitorInterval(opts)

	var group sync.WaitGroup
	for worker := range opts.Visitors {
		group.Add(1)
		go func() {
			defer group.Done()
			// Seeded per visitor and deterministically, so a run is reproducible
			// and two visitors do not walk the same sequence.
			random := rand.New(rand.NewPCG(uint64(worker)+1, 0x9E3779B9))
			driveVisitor(runCtx, opts, client, tally, random, interval)
		}()
	}
	group.Wait()

	result := tally.result()
	log.Info("browser door finished",
		slog.Int64("page_loads", result.PageLoads),
		slog.Int64("web_vitals", result.Vitals),
		slog.Int64("errors", result.Errors),
		slog.Int64("exports_accepted", result.Accepted),
		slog.Int64("exports_refused", result.Refused),
	)
	return result, nil
}

// driveVisitor is one simulated browser loading pages until told to stop.
func driveVisitor(
	ctx context.Context,
	opts Options,
	client *http.Client,
	tally *counters,
	random *rand.Rand,
	interval time.Duration,
) {
	visitor := newVisitor(random, opts.Origin)
	for {
		if ctx.Err() != nil {
			return
		}
		loadOnePage(ctx, opts, client, tally, random, visitor)
		if interval == 0 {
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(interval):
		}
	}
}

// loadOnePage sends one page load's vitals, and its exception when it threw.
//
// Both travel in one export, which is what the SDK does: it batches by time so
// the unload beacon is a single request inside the browser's own quota.
func loadOnePage(
	ctx context.Context,
	opts Options,
	client *http.Client,
	tally *counters,
	random *rand.Rand,
	visitor visitor,
) {
	now := time.Now().UTC()
	path := pagePaths[random.IntN(len(pagePaths))]

	records := visitor.vitalsFor(random, path, now)
	vitals := len(records)
	errors := 0
	if random.Float64() < opts.ErrorRate {
		records = append(records, visitor.errorFor(random, path, now, opts.Release))
		errors = 1
	}

	body, err := logsExport(visitor.resource(opts.ServiceName, opts.Release), records)
	if err != nil {
		tally.refuse(fmt.Sprintf("building the export: %v", err))
		return
	}
	if !send(ctx, client, opts, body, tally) {
		return
	}
	tally.accept(int64(vitals), int64(errors))
}

// send posts one export and reports whether the door took it.
func send(
	ctx context.Context,
	client *http.Client,
	opts Options,
	body []byte,
	tally *counters,
) bool {
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, opts.Endpoint+logsPath, bytes.NewReader(body))
	if err != nil {
		tally.refuse(err.Error())
		return false
	}
	// Content-Type must be explicit: an absent one selects the protobuf decoder
	// and every JSON body then fails to parse.
	request.Header.Set("content-type", "application/json")
	request.Header.Set(siteKeyHeader, opts.SiteKey)
	// The door checks this, and a browser is the only thing that sets it
	// honestly. A run without it measures a door no page reaches.
	request.Header.Set("origin", opts.Origin)

	response, err := client.Do(request)
	if err != nil {
		// A cancelled run is the duration elapsing, not a refusal.
		if ctx.Err() == nil {
			tally.refuse(err.Error())
		}
		return false
	}
	defer func() { _ = response.Body.Close() }()

	if response.StatusCode >= http.StatusBadRequest {
		tally.refuse(fmt.Sprintf("the door answered %d", response.StatusCode))
		return false
	}
	return true
}

// perVisitorInterval turns an offered rate across every visitor into the wait
// each one takes between page loads. Zero rate means no throttle at all.
func perVisitorInterval(opts Options) time.Duration {
	if opts.PageLoadsPerSecond <= 0 || opts.Visitors <= 0 {
		return 0
	}
	perVisitor := opts.PageLoadsPerSecond / float64(opts.Visitors)
	if perVisitor <= 0 {
		return 0
	}
	return time.Duration(float64(time.Second) / perVisitor)
}

// counters is the tally, shared across visitors.
type counters struct {
	pageLoads atomic.Int64
	vitals    atomic.Int64
	errors    atomic.Int64
	accepted  atomic.Int64
	refused   atomic.Int64

	once         sync.Once
	firstRefusal string
}

func (c *counters) accept(vitals, errors int64) {
	c.pageLoads.Add(1)
	c.vitals.Add(vitals)
	c.errors.Add(errors)
	c.accepted.Add(1)
}

func (c *counters) refuse(reason string) {
	c.refused.Add(1)
	c.once.Do(func() { c.firstRefusal = reason })
}

func (c *counters) result() Result {
	return Result{
		PageLoads:    c.pageLoads.Load(),
		Vitals:       c.vitals.Load(),
		Errors:       c.errors.Load(),
		Accepted:     c.accepted.Load(),
		Refused:      c.refused.Load(),
		FirstRefusal: c.firstRefusal,
	}
}
