//go:build chaos

// Package chaos proves invariant 2 — no data loss — by breaking the pipeline
// underneath a running load and checking that nothing acknowledged went missing.
//
// It is build-tagged because it kills infrastructure. `just check` must never run
// it; `just chaos` does, deliberately, against a stack you are willing to lose.
//
// The assertion rests on what the receiver *acknowledged*. While Kafka is down the
// receiver answers 503 and the load generator records those spans as offered but
// not accepted — which is the invariant working, not a loss. So the question is
// narrow and answerable: of everything we told a client we had taken, did all of
// it become queryable?
//
// Exact counters are the thing compared, not stored spans. Counters are derived
// before the sampler (ADR 0010), so they are the pre-sampling truth; stored spans
// are 10% of healthy traces by design and would make a loss and a sampling
// decision indistinguishable.
package chaos

import (
	"bufio"
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
)

const (
	composeFile = "../../deploy/compose/docker-compose.yaml"

	loadDuration = 180 * time.Second
	outageLength = 20 * time.Second
	killKafkaAt  = 40 * time.Second
	killStoreAt  = 110 * time.Second

	// Generous: the worker holds a trace for the 10s sampling window (ADR 0058),
	// then commits only once the minute's counters are written, so offsets trail
	// ingest by up to about 70s even when nothing is broken.
	drainTimeout = 5 * time.Minute
	drainPoll    = 5 * time.Second

	clickHouseURL  = "http://localhost:18123/"
	clickHouseUser = "nexovue"
	clickHousePass = "nexovue_dev"
)

var spansAcceptedLine = regexp.MustCompile(`spans accepted\s+(\d+)`)

func TestNothingAcknowledgedIsEverLost(t *testing.T) {
	service := fmt.Sprintf("chaos-%d", time.Now().UnixNano())
	t.Logf("service under test: %s", service)

	requireHealthy(t)
	t.Cleanup(func() { restoreStack(t) })

	// The demo app produces continuously, and consumer lag is per-partition, not
	// per-service. Quieten it so lag reaching zero means this run has drained.
	quietenDemo(t)

	// The queue is not empty at the start — earlier work parked poison messages
	// deliberately, and nothing drains it by design. Only the delta is ours.
	dlqBefore := dlqDepth(t)

	// Break things while the load is mid-flight. Both outages are timed from the
	// start of the run rather than chained, so a slow restart cannot push the
	// second failure past the end of the load.
	done := make(chan string, 1)
	go func() { done <- runLoad(t, service) }()

	sleep(t, killKafkaAt)
	outage(t, "kafka")
	sleep(t, killStoreAt-killKafkaAt-outageLength)
	outage(t, "clickhouse")

	output := <-done
	accepted := parseAccepted(t, output)
	if accepted == 0 {
		t.Fatal("the receiver accepted nothing; the run proves nothing about loss")
	}
	t.Logf("receiver acknowledged %d spans", accepted)

	counted := waitForDrain(t, service, accepted)

	switch {
	case counted < accepted:
		t.Errorf("DATA LOSS: acknowledged %d spans, only %d were counted (%d missing)",
			accepted, counted, accepted-counted)
	case counted > accepted:
		// ADR 0060 opened ClickHouse's insert-deduplication window so a replayed
		// counter block is dropped. It is proven for a worker restart; whether the
		// dedup log survives a *ClickHouse* restart is undocumented, and this run
		// is where that gets answered.
		t.Errorf("DOUBLE COUNTING: acknowledged %d spans but counted %d (%d extra) — "+
			"insert deduplication did not survive the outage; ADR 0060 needs the durable "+
			"watermark instead", accepted, counted, counted-accepted)
	default:
		t.Logf("zero loss: %d acknowledged, %d counted, exactly", accepted, counted)
	}

	if parked := dlqDepth(t) - dlqBefore; parked > 0 {
		t.Errorf("%d messages were parked on the dead-letter queue during the run; an "+
			"outage must not make a well-formed envelope undecodable", parked)
	}
}

// runLoad drives the generator and returns its report.
func runLoad(t *testing.T, service string) string {
	t.Helper()
	cmd := exec.Command("go", "run", "./tools/loadgen")
	cmd.Dir = "../.."
	cmd.Env = append(os.Environ(),
		"LOADGEN_SERVICE_NAME="+service,
		"LOADGEN_DURATION="+loadDuration.String(),
		// Well under the receiver's 1,000 req/s limit and enough to keep the
		// pipeline busy across both outages without saturating the laptop.
		"LOADGEN_SPAN_RATE=2000",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		// A non-zero exit is expected: the generator meets 503s while Kafka is
		// down. The report still tells us what was acknowledged.
		t.Logf("load generator exited with %v (expected during an outage)", err)
	}
	return string(out)
}

func parseAccepted(t *testing.T, report string) int {
	t.Helper()
	match := spansAcceptedLine.FindStringSubmatch(report)
	if match == nil {
		t.Fatalf("no 'spans accepted' line in the report:\n%s", tail(report, 30))
	}
	accepted, err := strconv.Atoi(match[1])
	if err != nil {
		t.Fatalf("unreadable accepted count %q: %v", match[1], err)
	}
	return accepted
}

// outage kills a service, leaves it down, and brings it back.
func outage(t *testing.T, service string) {
	t.Helper()
	t.Logf("killing %s", service)
	compose(t, "kill", service)
	sleep(t, outageLength)
	t.Logf("restarting %s", service)
	compose(t, "up", "-d", "--wait", service)
	t.Logf("%s is back", service)
}

// waitForDrain waits until the consumer group has caught up, then returns what
// was counted.
//
// It waits on the broker's own lag rather than on the counter going still. A
// counter that stops moving is ambiguous — it looks identical whether the
// pipeline drained or wedged — and the first version of this test called a
// wedged consumer "data loss" when every message was still sitting in Kafka.
// Lag tells the two apart, so the diagnosis is the true one.
func waitForDrain(t *testing.T, service string, target int) int {
	t.Helper()
	deadline := time.Now().Add(drainTimeout)

	for time.Now().Before(deadline) {
		time.Sleep(drainPoll)
		lag, counted := consumerLag(t), countedSpans(t, service)
		if lag == 0 {
			// The window and the commit trail mean counters land a little after
			// the last fetch; give them room before reading the final number.
			t.Logf("consumer caught up; counted %d of %d", counted, target)
			time.Sleep(20 * time.Second)
			return countedSpans(t, service)
		}
		t.Logf("draining… lag %d, counted %d of %d", lag, counted, target)
	}

	if lag := consumerLag(t); lag > 0 {
		t.Errorf("PIPELINE STALLED: %d messages still unconsumed after %s. Nothing was "+
			"lost — the worker stopped fetching. Check whether it wedged on a call with "+
			"no deadline rather than treating this as loss", lag, drainTimeout)
	}
	return countedSpans(t, service)
}

// consumerLag is how far behind the group is, summed over partitions.
func consumerLag(t *testing.T) int {
	t.Helper()
	out := compose(t, "exec", "-T", "kafka",
		"/opt/kafka/bin/kafka-consumer-groups.sh", "--bootstrap-server", "kafka:9092",
		"--describe", "--group", "nexovue-workers")
	total := 0
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 6 {
			continue
		}
		if lag, err := strconv.Atoi(fields[5]); err == nil {
			total += lag
		}
	}
	return total
}

// quietenDemo stops the demo services for the duration of the run and brings
// them back afterwards.
func quietenDemo(t *testing.T) {
	t.Helper()
	demo := []string{"demo-storefront", "demo-checkout", "demo-payments"}
	compose(t, append([]string{"stop"}, demo...)...)
	t.Cleanup(func() {
		compose(t, append([]string{"start"}, demo...)...)
	})
}

func countedSpans(t *testing.T, service string) int {
	t.Helper()
	answer := query(t, fmt.Sprintf(
		"SELECT toUInt64(sum(value)) FROM metrics WHERE metric_name = 'nexovue.spans.count' "+
			"AND service_name = '%s'", service))
	if answer == "" {
		return 0
	}
	counted, err := strconv.Atoi(answer)
	if err != nil {
		t.Fatalf("unreadable counter %q: %v", answer, err)
	}
	return counted
}

func dlqDepth(t *testing.T) int {
	t.Helper()
	out := compose(t, "exec", "-T", "kafka",
		"/opt/kafka/bin/kafka-get-offsets.sh", "--bootstrap-server", "kafka:9092", "--topic", "spans-dlq")
	total := 0
	for _, line := range strings.Split(out, "\n") {
		parts := strings.Split(strings.TrimSpace(line), ":")
		if len(parts) != 3 {
			continue
		}
		if offset, err := strconv.Atoi(parts[2]); err == nil {
			total += offset
		}
	}
	return total
}

// query runs one read against ClickHouse over HTTP, which needs no driver and no
// container exec — so it still answers while the rest of the stack is unhappy.
func query(t *testing.T, sql string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	endpoint := clickHouseURL + "?" + url.Values{"database": {"nexovue"}, "query": {sql}}.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		t.Fatalf("build query: %v", err)
	}
	request.SetBasicAuth(clickHouseUser, clickHousePass)

	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Logf("clickhouse unreachable: %v", err)
		return ""
	}
	defer response.Body.Close()

	body := new(strings.Builder)
	scanner := bufio.NewScanner(response.Body)
	for scanner.Scan() {
		body.WriteString(scanner.Text())
	}
	if response.StatusCode != http.StatusOK {
		t.Logf("clickhouse answered %d: %s", response.StatusCode, body.String())
		return ""
	}
	return strings.TrimSpace(body.String())
}

func compose(t *testing.T, args ...string) string {
	t.Helper()
	full := append([]string{"compose", "-f", composeFile}, args...)
	out, err := exec.Command("docker", full...).CombinedOutput()
	if err != nil {
		t.Logf("docker %s: %v\n%s", strings.Join(args, " "), err, tail(string(out), 5))
	}
	return string(out)
}

func requireHealthy(t *testing.T) {
	t.Helper()
	if query(t, "SELECT 1") != "1" {
		t.Skip("dev stack is not up; run `just dev` first")
	}
}

// restoreStack brings everything back regardless of how the test ended, so a
// failure does not leave the machine with a dead broker.
func restoreStack(t *testing.T) {
	t.Helper()
	t.Log("restoring the stack")
	compose(t, "up", "-d", "--wait", "kafka", "clickhouse", "receiver", "worker")
}

func sleep(t *testing.T, d time.Duration) {
	t.Helper()
	if d > 0 {
		time.Sleep(d)
	}
}

func tail(s string, lines int) string {
	all := strings.Split(strings.TrimRight(s, "\n"), "\n")
	if len(all) <= lines {
		return s
	}
	return strings.Join(all[len(all)-lines:], "\n")
}
