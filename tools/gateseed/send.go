package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	ingestKeyHeader = "x-nexovue-key"
	tracesPath      = "/v1/traces"
	metricsPath     = "/v1/metrics"
	// The receiver reads OTLP/JSON on its server doors, so nothing here needs a
	// protobuf toolchain or an SDK — which keeps the seeder small enough to read
	// in one sitting and to be obviously sending what it says it sends.
	contentTypeJSON = "application/json"
)

// export posts one OTLP/JSON body to a signal's door.
//
// The response is read before it matters: the receiver answers only after Kafka
// has acknowledged, so a 200 here means accepted for delivery — not yet
// readable, which is what `WaitFor` is for.
func export(ctx context.Context, client *http.Client, cfg Config, path string, body map[string]any) error {
	encoded, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("encoding the %s payload: %w", strings.TrimPrefix(path, "/v1/"), err)
	}

	endpoint := strings.TrimSuffix(cfg.Endpoint, "/") + path
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		return fmt.Errorf("building the request to %s: %w", endpoint, err)
	}
	request.Header.Set("Content-Type", contentTypeJSON)
	request.Header.Set(ingestKeyHeader, cfg.IngestKey)

	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("posting to %s: %w", endpoint, err)
	}
	defer func() { _ = response.Body.Close() }()
	answer, _ := io.ReadAll(io.LimitReader(response.Body, 2048))
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("%s answered %d: %s", endpoint, response.StatusCode, bytes.TrimSpace(answer))
	}
	return nil
}

// query asks ClickHouse one question and returns its single-line answer.
func query(ctx context.Context, client *http.Client, ch ClickHouse, statement string) (string, error) {
	endpoint, err := url.Parse(strings.TrimSuffix(ch.URL, "/") + "/")
	if err != nil {
		return "", fmt.Errorf("parsing the ClickHouse URL %q: %w", ch.URL, err)
	}
	parameters := url.Values{"database": {ch.Database}, "default_format": {"TabSeparated"}}
	endpoint.RawQuery = parameters.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), strings.NewReader(statement))
	if err != nil {
		return "", fmt.Errorf("building the ClickHouse request: %w", err)
	}
	request.SetBasicAuth(ch.User, ch.Password)

	response, err := client.Do(request)
	if err != nil {
		return "", fmt.Errorf("asking ClickHouse: %w", err)
	}
	defer func() { _ = response.Body.Close() }()
	answer, err := io.ReadAll(io.LimitReader(response.Body, 8192))
	if err != nil {
		return "", fmt.Errorf("reading ClickHouse's answer: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("ClickHouse answered %d: %s", response.StatusCode, bytes.TrimSpace(answer))
	}
	return strings.TrimSpace(string(answer)), nil
}

// Arrival is one thing the seeder waits to become readable.
//
// **Counted against a baseline taken before the export, not against a clock.**
// The first attempt bounded the count by `received_at >= now - 1m`, and a second
// run inside that minute was satisfied instantly by the first run's rows:
// measured 12.3s, then 7ms, then 8ms — the wait it exists for, skipped. A
// baseline needs no agreement between this machine's clock and ClickHouse's.
type Arrival struct {
	What      string
	Statement string
	// Baseline is what the statement counted before anything was sent.
	Baseline int
	// Expect is how many rows this run adds on top of the baseline.
	Expect int
}

// Baselines counts each arrival as it stands before anything is sent.
func Baselines(ctx context.Context, client *http.Client, cfg Config, arrivals []Arrival) ([]Arrival, error) {
	counted := make([]Arrival, 0, len(arrivals))
	for _, arrival := range arrivals {
		answer, err := query(ctx, client, cfg.ClickHouse, arrival.Statement)
		if err != nil {
			return nil, err
		}
		var at int
		// An unreadable answer is a baseline of zero, which only ever makes the
		// wait stricter.
		_, _ = fmt.Sscanf(answer, "%d", &at)
		arrival.Baseline = at
		counted = append(counted, arrival)
	}
	return counted, nil
}

// WaitFor blocks until every arrival is readable, or until the budget runs out.
//
// **The gate would otherwise race its own seed.** A 200 from the receiver means
// Kafka holds the batch; the worker still has to consume it, mask it and have
// ClickHouse acknowledge the insert. Returning before that and letting pytest
// start is how a seeded gate would fail more confusingly than an unseeded one.
func WaitFor(ctx context.Context, client *http.Client, cfg Config, arrivals []Arrival) error {
	deadline := time.Now().Add(cfg.Wait)
	pending := arrivals

	for {
		still := pending[:0:0]
		for _, arrival := range pending {
			answer, err := query(ctx, client, cfg.ClickHouse, arrival.Statement)
			if err != nil {
				return err
			}
			if countedAtLeast(answer, arrival.Baseline+arrival.Expect) {
				continue
			}
			still = append(still, arrival)
		}
		pending = still
		if len(pending) == 0 {
			return nil
		}
		if time.Now().After(deadline) {
			missing := make([]string, 0, len(pending))
			for _, arrival := range pending {
				missing = append(missing, arrival.What)
			}
			return fmt.Errorf(
				"waited %s and these never arrived: %s. The receiver accepted "+
					"them, so look at the worker rather than at this tool: "+
					"docker compose -f deploy/compose/docker-compose.yaml logs worker",
				cfg.Wait, strings.Join(missing, ", "),
			)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(cfg.Poll):
		}
	}
}

func countedAtLeast(answer string, atLeast int) bool {
	var counted int
	if _, err := fmt.Sscanf(answer, "%d", &counted); err != nil {
		return false
	}
	return counted >= atLeast
}
