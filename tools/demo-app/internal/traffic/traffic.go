// Package traffic drives the shop against itself, so the stack always has
// something to look at without anyone opening a browser.
package traffic

import (
	"context"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"time"
)

// browseShare is how often a request is a cheap browse rather than a checkout.
// Real traffic is mostly reads, and a trace list where everything looks the
// same is a poor test of a screen meant to make one thing stand out.
const browseShare = 0.7

// Generator issues requests at a steady rate against its own storefront.
type Generator struct {
	baseURL string
	rate    float64
	client  *http.Client
	log     *slog.Logger
}

// New builds a generator issuing `rate` requests per second at baseURL.
func New(baseURL string, rate float64, log *slog.Logger) *Generator {
	return &Generator{
		baseURL: baseURL,
		rate:    rate,
		// Uninstrumented on purpose: this stands in for a browser, and a
		// browser is not part of the trace it starts.
		client: &http.Client{Timeout: 10 * time.Second},
		log:    log,
	}
}

// Run issues requests until the context is cancelled.
func (g *Generator) Run(ctx context.Context) {
	if g.rate <= 0 {
		g.log.InfoContext(ctx, "traffic generator disabled")
		return
	}

	interval := time.Duration(float64(time.Second) / g.rate)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	g.log.InfoContext(ctx, "traffic generator started",
		slog.Float64("requests_per_second", g.rate))

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			g.once(ctx)
		}
	}
}

func (g *Generator) once(ctx context.Context) {
	method, path := http.MethodPost, "/checkout"
	if rand.Float64() < browseShare {
		method, path = http.MethodGet, "/browse"
	}

	req, err := http.NewRequestWithContext(ctx, method, g.baseURL+path, nil)
	if err != nil {
		return
	}
	resp, err := g.client.Do(req)
	if err != nil {
		// A failed request is usually a fault doing its job, so this is debug
		// rather than error: the interesting signal is in the telemetry.
		g.log.DebugContext(ctx, "request failed", slog.String("path", path), slog.Any("error", err))
		return
	}
	_ = resp.Body.Close()
}
