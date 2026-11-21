// Command gateseed puts the telemetry the live read suites need into the
// development stack, through the ingest path a customer uses.
//
// **Why this exists.** Five suites check the read paths against real telemetry
// rather than fixtures, and that is deliberate: it is how we know the query
// behind the metrics screen survives real histogram shapes. Each asks whether
// there is data inside a rolling window and *fails* when there is not, because
// a skip is indistinguishable from a pass.
//
// The data used to come only from generators run by hand — `just load`, and the
// OpenTelemetry demo's twenty-one containers for anything else. So the gate went
// red on a clean tree after two quiet days. Measured 2026-09-21: three metrics
// tests turned red six minutes after a green run on the same commit, because the
// newest histogram point crossed a two-day lookback. A gate that fails for a
// reason unrelated to the code is a gate people learn to ignore.
//
// This sends the four things those suites cannot find for themselves — traces,
// a span carrying an exception event, a cumulative histogram, and three
// gen_ai model-call spans for F29 — and waits for the ingest path to make them
// readable before it returns.
//
// It is not a load generator. It sends the smallest amount that satisfies the
// assertions; `just load` still exists for measuring throughput.
package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "gateseed: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := Load()
	if err != nil {
		return err
	}
	plantedText, planted, err := logBodyArgument(os.Args[1:])
	if err != nil {
		return err
	}
	// A planted line is a person's explicit ask (F4 T1.0), not a gate run, so
	// it is not silenced by the gate's skip switch.
	if cfg.Disabled && !planted {
		fmt.Println("gateseed: NEXOVUE_SKIP_LIVE_TESTS is set — seeding nothing.")
		return nil
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	client := &http.Client{Timeout: cfg.RequestTimeout}
	if planted {
		return plantLogLine(ctx, client, cfg, plantedText)
	}
	at := time.Now().UTC()

	gateCheck, err := EnsureGateCheck(ctx, cfg)
	if err != nil {
		return err
	}

	// Counted first, so the wait below is for *this* run's rows and not for rows
	// a run a minute ago left behind.
	expected, err := Baselines(ctx, client, cfg, arrivals(cfg, gateCheck))
	if err != nil {
		return err
	}

	if err := export(ctx, client, cfg, tracesPath, Traces(cfg, cfg.Traces, at)); err != nil {
		return err
	}
	if err := export(ctx, client, cfg, metricsPath, Metrics(cfg, at)); err != nil {
		return err
	}
	if err := export(ctx, client, cfg, tracesPath, GenAISpans(cfg, at)); err != nil {
		return err
	}
	if err := export(ctx, client, cfg, tracesPath, ServiceEdges(cfg, at.Add(-EdgeLead))); err != nil {
		return err
	}
	if err := export(ctx, client, cfg, metricsPath, DatabaseReadings(cfg, at)); err != nil {
		return err
	}

	started := time.Now()
	if err := WaitFor(ctx, client, cfg, expected); err != nil {
		return err
	}
	fmt.Printf(
		"gateseed: %d traces, one exception, %d histogram points, three gen_ai calls, two "+
			"service edges, four database readings and a passing probe readable after %s.\n",
		cfg.Traces, cfg.Series*cfg.Exports, time.Since(started).Round(time.Millisecond),
	)
	return nil
}

// arrivals is what must become readable before the suites may run, and how many
// rows of each this run adds.
//
// No time bound on the statements: `Baselines` counts each one before anything
// is sent and the wait is for that many more. Rows this tool wrote days ago are
// in the baseline, so they can neither satisfy the wait nor break it.
func arrivals(cfg Config, gateCheck string) []Arrival {
	return []Arrival{
		{
			What:   "the seeded traces",
			Expect: cfg.Traces,
			Statement: fmt.Sprintf(
				"SELECT count() FROM spans WHERE service_name = '%s'", cfg.ServiceName,
			),
		},
		{
			What:   "the seeded exception",
			Expect: 1,
			Statement: fmt.Sprintf(
				"SELECT count() FROM spans WHERE service_name = '%s' AND exception_type != ''",
				cfg.ServiceName,
			),
		},
		{
			What:   "the seeded histogram",
			Expect: cfg.Series * cfg.Exports,
			Statement: fmt.Sprintf(
				"SELECT count() FROM metrics_histogram WHERE metric_name = '%s'", cfg.MetricName,
			),
		},
		{
			// All three of GenAISpans' spans set this field, including the one
			// standing in for a future rename of the model-name field — only the
			// model name is renamed there, not the provider, so this still counts
			// it as a gen_ai call.
			What:   "the seeded gen_ai calls",
			Expect: 3,
			Statement: fmt.Sprintf(
				"SELECT count() FROM spans WHERE service_name = '%s' "+
					"AND toString(getSubcolumn(span_attrs, 'gen_ai.provider.name')) != ''",
				cfg.ServiceName,
			),
		},
		{
			What:   "the seeded service edges",
			Expect: 2,
			Statement: fmt.Sprintf(
				"SELECT count() FROM service_edges_1m WHERE client_service = '%s'", callerService(cfg),
			),
		},
		{
			What:   "the seeded database readings",
			Expect: 4,
			Statement: fmt.Sprintf(
				"SELECT count() FROM metrics_gauge WHERE metric_name IN "+
					"('db.client.operation.duration', 'db.client.connection.count') "+
					"AND toString(resource_attrs.server.address) = '%s'", DatabaseServer,
			),
		},
		{
			// Not this run's row: the scheduler probes on its own clock. What the
			// synthetics suite needs is a passing probe of a target the stack
			// always runs within the hour it reads, so that is what is waited for —
			// instantly on every run but the one that created the check.
			What:   "a passing probe of the gate's own check",
			Expect: 1,
			Exists: true,
			Statement: fmt.Sprintf(
				"SELECT count() FROM metrics_gauge WHERE metric_name = 'synthetic.check.status' "+
					"AND toString(resource_attrs.synthetic.check.id) = '%s' "+
					"AND toString(attrs.synthetic.check.outcome) = 'up' "+
					"AND timestamp >= now() - INTERVAL 1 HOUR", gateCheck,
			),
		},
	}
}

// logBodyArgument reads the one option this tool takes: `--log-body "text"`,
// in either spelling. Anything else on the command line is a mistake worth
// stopping on — seeding something other than what was asked for is worse than
// refusing.
func logBodyArgument(args []string) (text string, planted bool, err error) {
	const flag = "--log-body"
	switch {
	case len(args) == 0:
		return "", false, nil
	case len(args) == 1 && strings.HasPrefix(args[0], flag+"=") && args[0] != flag+"=":
		return strings.TrimPrefix(args[0], flag+"="), true, nil
	case len(args) == 2 && args[0] == flag && args[1] != "":
		return args[1], true, nil
	}
	return "", false, fmt.Errorf(`usage: gateseed [--log-body "one chosen log line"]`)
}

// plantLogLine sends one chosen log record through the receiver with the dev
// key and waits until it is readable, so the demonstration that follows —
// asking a connected assistant about it — never reads an empty window.
func plantLogLine(ctx context.Context, client *http.Client, cfg Config, text string) error {
	expected, err := Baselines(ctx, client, cfg, []Arrival{{
		What:   "the planted log line",
		Expect: 1,
		// Counted by service rather than by body text: the text is anybody's
		// chosen string, and splicing it into SQL is the mistake this avoids.
		Statement: fmt.Sprintf(
			"SELECT count() FROM logs WHERE service_name = '%s'", cfg.ServiceName,
		),
	}})
	if err != nil {
		return err
	}
	if err := export(ctx, client, cfg, logsPath, LogBody(cfg, text, time.Now().UTC())); err != nil {
		return err
	}
	started := time.Now()
	if err := WaitFor(ctx, client, cfg, expected); err != nil {
		return err
	}
	fmt.Printf(
		"gateseed: one log line for service %q readable after %s. Ask the assistant about the last hour.\n",
		cfg.ServiceName, time.Since(started).Round(time.Millisecond),
	)
	return nil
}
