// Command loadgen drives synthetic OTLP/HTTP traffic at the receiver and
// reports the numbers behind the performance budgets in §4 of the phase 1 plan.
//
// Spans come from telemetrygen, so the protocol on the wire is the real one and
// no application has to be deployed to produce it. Every export is authenticated
// with an ingest key: the receiver's auth and rate-limit gates run ahead of
// decoding, so a run that skipped them would be measuring a receiver we do not
// ship.
//
// Settings are environment variables with working defaults — see the README.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/config"
	"github.com/nexovue-tech/nexovue/tools/loadgen/internal/run"
)

func main() {
	if err := load(); err != nil {
		fmt.Fprintf(os.Stderr, "loadgen: %v\n", err)
		os.Exit(1)
	}
}

func load() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	report, err := run.Load(ctx, cfg, log)
	if err != nil {
		return err
	}

	return report.Write(os.Stdout)
}
