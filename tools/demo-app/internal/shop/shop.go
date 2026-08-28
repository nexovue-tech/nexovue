// Package shop is the fake storefront: three roles that call each other over
// HTTP so a trace has real depth and crosses real service boundaries.
//
//	storefront  GET /browse, POST /checkout
//	  └── checkout  POST /checkout          (+ a database span)
//	        └── payments  POST /charge
package shop

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"time"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/trace"

	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/config"
	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/faults"
)

// Work that a real service would actually do takes time; these stand in for it.
const (
	catalogueQueryTime = 12 * time.Millisecond
	cartQueryTime      = 18 * time.Millisecond
	paymentCallTime    = 40 * time.Millisecond
)

// Service is one role of the shop.
type Service struct {
	cfg    config.Config
	faults *faults.Registry
	client *http.Client
	tracer trace.Tracer
	// log goes through the OTLP bridge, so an ordinary slog call inside a span
	// becomes a log record carrying that span's trace and span ids. That is
	// what makes "the logs for this trace" answerable at all.
	log *slog.Logger
	// charges counts payment authorizations by outcome, and chargeLatency
	// records how long each took — the two shapes a customer's own metrics
	// arrive in, so both the counter and the histogram paths are exercised.
	charges       metric.Int64Counter
	chargeLatency metric.Float64Histogram
}

// New builds the service for the role in cfg.
func New(cfg config.Config, reg *faults.Registry) *Service {
	s := &Service{
		cfg:    cfg,
		faults: reg,
		// The transport is instrumented, which is what injects traceparent on
		// the way out and makes the next hop a child rather than a new trace.
		client: &http.Client{
			Timeout:   config.RequestTimeout,
			Transport: otelhttp.NewTransport(http.DefaultTransport),
		},
		tracer: otel.Tracer("demo-app/" + string(cfg.Role)),
		log:    otelslog.NewLogger("demo-app/" + string(cfg.Role)),
	}
	return s.instruments()
}

// instruments builds the meters. A failure here is not fatal: a demo that
// cannot count should still serve traffic, and a nil instrument is a no-op.
func (s *Service) instruments() *Service {
	meter := otel.Meter("demo-app/" + string(s.cfg.Role))
	s.charges, _ = meter.Int64Counter("demo.payment.authorizations",
		metric.WithDescription("Payment authorizations attempted, by outcome"),
		metric.WithUnit("{authorization}"))
	s.chargeLatency, _ = meter.Float64Histogram("demo.payment.duration",
		metric.WithDescription("Time spent authorizing a payment"),
		metric.WithUnit("ms"))
	return s
}

// Handler is this role's HTTP surface, instrumented so every inbound request
// starts (or continues) a trace.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()

	switch s.cfg.Role {
	case config.RoleStorefront:
		mux.HandleFunc("GET /browse", s.browse)
		mux.HandleFunc("POST /checkout", s.storefrontCheckout)
	case config.RoleCheckout:
		mux.HandleFunc("POST /checkout", s.checkout)
	case config.RolePayments:
		mux.HandleFunc("POST /charge", s.charge)
	}

	mux.Handle("/", s.faults.Handler())
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	// otelhttp names the server span from the matched route, which is what
	// makes "slowest endpoints" a meaningful question later.
	return otelhttp.NewHandler(mux, "demo-app",
		otelhttp.WithSpanNameFormatter(func(_ string, r *http.Request) string {
			return r.Method + " " + r.URL.Path
		}),
	)
}

func (s *Service) browse(w http.ResponseWriter, r *http.Request) {
	ctx, span := s.tracer.Start(r.Context(), "catalogue.query")
	span.SetAttributes(
		attribute.String("db.system", "postgresql"),
		attribute.String("db.statement", "SELECT id, name, price FROM products LIMIT 20"),
	)
	sleep(ctx, catalogueQueryTime)
	span.End()

	writeJSON(w, http.StatusOK, map[string]any{"products": rand.IntN(20) + 1})
}

// storefrontCheckout is the entry point the traffic generator drives. It is the
// span that a user would recognise as "checkout was slow".
func (s *Service) storefrontCheckout(w http.ResponseWriter, r *http.Request) {
	if err := s.call(r.Context(), http.MethodPost, s.cfg.CheckoutURL+"/checkout"); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"order": rand.Uint32()})
}

func (s *Service) checkout(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	if err := s.cartQuery(ctx); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if err := s.call(ctx, http.MethodPost, s.cfg.PaymentsURL+"/charge"); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "confirmed"})
}

// cartQuery is the span the checkout-db faults act on: the "unindexed query
// after a deploy" story, which is the canonical thing our insight engine will
// have to explain.
func (s *Service) cartQuery(ctx context.Context) error {
	ctx, span := s.tracer.Start(ctx, "cart.query")
	defer span.End()
	span.SetAttributes(
		attribute.String("db.system", "postgresql"),
		attribute.String("db.statement", "SELECT * FROM cart_items WHERE user_id = $1"),
	)

	delay := cartQueryTime
	if s.faults.Active(faults.CheckoutDBSlow) {
		delay += faults.SlowPenalty
		span.SetAttributes(attribute.Bool("demo.fault.slow", true))
	}
	sleep(ctx, delay)

	if s.faults.Active(faults.CheckoutDBDown) {
		err := errors.New("connection refused: cart database is unreachable")
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return err
	}
	return nil
}

func (s *Service) charge(w http.ResponseWriter, r *http.Request) {
	ctx, span := s.tracer.Start(r.Context(), "payment.authorize")
	defer span.End()
	span.SetAttributes(attribute.String("payment.provider", "stripe-sandbox"))

	started := time.Now()
	delay := paymentCallTime
	if s.faults.Active(faults.PaymentsSlow) {
		delay += faults.SlowPenalty
	}
	sleep(ctx, delay)

	if s.faults.Active(faults.PaymentsErrors) && rand.Float64() < faults.ErrorShare {
		err := errors.New("card authorization declined by upstream")
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		// Logged inside the span, so this record carries the trace it belongs
		// to. The authorization header is deliberately in the message: a real
		// service logs the request it made, and this is what the masking rule
		// has to catch before it reaches storage.
		s.log.ErrorContext(ctx, "payment authorization failed",
			slog.String("provider", "stripe-sandbox"),
			slog.String("upstream_request", "POST /authorize Authorization: Bearer sk_test_51HxmpleTokenThatMustNeverBeStored"),
			slog.String("exception.type", "AuthorizationDeclined"),
			slog.String("exception.message", err.Error()))
		s.record(ctx, "declined", started)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	s.log.InfoContext(ctx, "payment authorized",
		slog.String("provider", "stripe-sandbox"))
	s.record(ctx, "authorized", started)
	writeJSON(w, http.StatusOK, map[string]any{"authorized": true})
}

// record counts one authorization and times it. Both instruments carry the
// outcome, so a rate and a latency distribution can be split the same way.
func (s *Service) record(ctx context.Context, outcome string, started time.Time) {
	if s.charges == nil {
		return
	}
	attrs := metric.WithAttributes(attribute.String("outcome", outcome))
	s.charges.Add(ctx, 1, attrs)
	s.chargeLatency.Record(ctx, float64(time.Since(started).Microseconds())/1000, attrs)
}

// call makes an instrumented request to an upstream role. The response body is
// drained so the connection can be reused rather than leaked per request.
func (s *Service) call(ctx context.Context, method, url string) error {
	req, err := http.NewRequestWithContext(ctx, method, url, nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}

	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("call %s: %w", url, err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("call %s: upstream returned %s", url, resp.Status)
	}
	return nil
}

// sleep respects cancellation, so a shutdown does not wait out every fake query.
func sleep(ctx context.Context, d time.Duration) {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
	case <-ctx.Done():
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
