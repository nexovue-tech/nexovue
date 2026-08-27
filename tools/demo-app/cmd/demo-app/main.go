// Command demo-app is a fake shop that emits real OpenTelemetry traces.
//
// It exists so the rest of the stack always has something to consume, and so
// the customer onboarding path — an OTel SDK pointed at our endpoint with two
// environment variables — is exercised every day rather than once before
// release. Faults are injected over HTTP at runtime; see the README.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/config"
	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/faults"
	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/shop"
	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/telemetry"
	"github.com/nexovue-tech/nexovue/tools/demo-app/internal/traffic"
)

// version is what the app reports as service.version; a release marker for the
// deploy-tracking screens later.
const version = "0.1.0"

const shutdownTimeout = 10 * time.Second

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "demo-app: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})).
		With(slog.String("role", string(cfg.Role)))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdownTelemetry, err := telemetry.Start(ctx, "demo-"+string(cfg.Role), version)
	if err != nil {
		return err
	}
	defer func() {
		// A fresh context: the one above is already cancelled by the time this
		// runs, and the exporter needs a moment to flush.
		if err := shutdownTelemetry(context.Background()); err != nil {
			log.Error("telemetry shutdown", slog.Any("error", err))
		}
	}()

	registry := faults.NewRegistry()
	server := &http.Server{
		Addr:              cfg.Addr,
		Handler:           shop.New(cfg, registry).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	if cfg.Role == config.RoleStorefront {
		go traffic.New("http://127.0.0.1"+cfg.Addr, cfg.TrafficRate, log).Run(ctx)
	}

	log.Info("demo-app starting",
		slog.String("addr", cfg.Addr),
		slog.String("otlp_endpoint", os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")),
		slog.Any("faults", faults.Names()),
	)

	return serve(ctx, log, server)
}

func serve(ctx context.Context, log *slog.Logger, server *http.Server) error {
	failed := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			failed <- err
		}
	}()

	select {
	case err := <-failed:
		return fmt.Errorf("http server: %w", err)
	case <-ctx.Done():
		log.Info("shutdown requested, draining")
	}

	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownTimeout)
	defer cancel()
	return server.Shutdown(drainCtx)
}
