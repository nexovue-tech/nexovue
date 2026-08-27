// Package faults makes the demo app break on command.
//
// Every fault is named, toggled at runtime over HTTP, and off by default. The
// point is to produce a *specific* failure on demand — "the checkout database
// got slow at 15:04" — because that is what the insight engine will later have
// to explain, and what a screen has to render convincingly before then.
package faults

import (
	"encoding/json"
	"net/http"
	"sort"
	"sync"
	"time"
)

// Name identifies a fault. They are deliberately concrete rather than generic
// ("error rate 5%"), because a real incident is always something in particular.
type Name string

const (
	// CheckoutDBSlow adds latency to the checkout database span, the classic
	// "someone dropped an index" shape.
	CheckoutDBSlow Name = "checkout-db-slow"
	// CheckoutDBDown fails the database span outright.
	CheckoutDBDown Name = "checkout-db-down"
	// PaymentsErrors makes the payments service return 500 for a share of calls.
	PaymentsErrors Name = "payments-errors"
	// PaymentsSlow adds latency to the payments call.
	PaymentsSlow Name = "payments-slow"
)

// All is every fault the app knows how to inject.
var All = []Name{CheckoutDBSlow, CheckoutDBDown, PaymentsErrors, PaymentsSlow}

// SlowPenalty is how much latency a "slow" fault adds. Fixed rather than random
// so the effect is unmistakable on a chart.
const SlowPenalty = 900 * time.Millisecond

// ErrorShare is the fraction of calls an "errors" fault fails. Not 100%, because
// a partial failure is both more realistic and harder for us to detect.
const ErrorShare = 0.35

// Registry holds which faults are currently active. Safe for concurrent use:
// the traffic generator reads it on every request while HTTP handlers flip it.
type Registry struct {
	mu     sync.RWMutex
	active map[Name]bool
}

// NewRegistry returns a registry with every fault switched off.
func NewRegistry() *Registry {
	return &Registry{active: make(map[Name]bool, len(All))}
}

// Active reports whether a fault is currently injected.
func (r *Registry) Active(name Name) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.active[name]
}

// Set turns a fault on or off. Takes effect on the next request.
func (r *Registry) Set(name Name, on bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.active[name] = on
}

func (r *Registry) snapshot() map[string]bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make(map[string]bool, len(All))
	for _, name := range All {
		out[string(name)] = r.active[name]
	}
	return out
}

// Handler serves the fault control surface:
//
//	GET  /faults              what is broken right now
//	POST /faults/{name}       break it
//	DELETE /faults/{name}     fix it
func (r *Registry) Handler() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /faults", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, r.snapshot())
	})

	toggle := func(on bool) http.HandlerFunc {
		return func(w http.ResponseWriter, req *http.Request) {
			name := Name(req.PathValue("name"))
			if !known(name) {
				writeJSON(w, http.StatusNotFound, map[string]any{
					"error": "no such fault", "known": All,
				})
				return
			}
			r.Set(name, on)
			writeJSON(w, http.StatusOK, r.snapshot())
		}
	}
	mux.Handle("POST /faults/{name}", toggle(true))
	mux.Handle("DELETE /faults/{name}", toggle(false))

	return mux
}

func known(name Name) bool {
	for _, n := range All {
		if n == name {
			return true
		}
	}
	return false
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// Names returns the fault names sorted, for a stable listing.
func Names() []string {
	out := make([]string, 0, len(All))
	for _, n := range All {
		out = append(out, string(n))
	}
	sort.Strings(out)
	return out
}
