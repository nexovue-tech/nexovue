package config

import (
	"fmt"
	"slices"
	"strings"
)

// Pipeline is one unit of work a worker process can run.
//
// They are separate names rather than one loop because they scale on different
// axes: trace assembly is memory-bound and grows with concurrent traces, log
// writing is I/O-bound, and metric ingest is a straight decode-and-write.
// Running them in one process is right until measurement says otherwise; the
// names are what make splitting later a deployment change rather than a
// rewrite.
//
// A name earns its place here only by starting something. Two did not and were
// removed.
type Pipeline string

// The pipelines a worker can run. The names are the values of
// WORKER_PIPELINES, so changing one is a deployment-visible change.
const (
	// PipelineTraceAssembly also derives the pre-sampling counters and the
	// service-edge rollup. Those were once separate names here, but nothing
	// started them: a process asked for `metric-derivation` alone ran no
	// pipeline at all and said at boot that it had. A name that cannot be
	// deployed on its own is not an affordance, it is a lie about one.
	PipelineTraceAssembly Pipeline = "trace-assembly"
	PipelineLogWriting    Pipeline = "log-writing"

	// PipelineMetricIngest writes customer-sent OTLP metrics. Distinct from
	// metric-derivation, which computes our own counters from traces: one reads
	// metrics-raw, the other reads nothing at all.
	PipelineMetricIngest Pipeline = "metric-ingest"

	// PipelineProfileIngest writes the profiles the receiver converted into
	// nexovue.v1.ProfileSamples (F17, ADR-015).
	PipelineProfileIngest Pipeline = "profile-ingest"
)

// AllPipelines is the default: one process runs everything.
var AllPipelines = []Pipeline{
	PipelineTraceAssembly,
	PipelineLogWriting,
	PipelineMetricIngest,
	PipelineProfileIngest,
}

// ParsePipelines turns the WORKER_PIPELINES value into the set this process
// should run. An unknown name is a startup failure rather than a silent
// omission, because a typo would otherwise mean a pipeline quietly not running.
func ParsePipelines(raw string) ([]Pipeline, error) {
	if strings.TrimSpace(raw) == "" || raw == "all" {
		return AllPipelines, nil
	}

	var selected []Pipeline
	for _, name := range strings.Split(raw, ",") {
		p := Pipeline(strings.TrimSpace(name))
		if !slices.Contains(AllPipelines, p) {
			return nil, fmt.Errorf(
				"WORKER_PIPELINES: unknown pipeline %q; valid names are %s",
				name, JoinPipelines(AllPipelines))
		}
		if !slices.Contains(selected, p) {
			selected = append(selected, p)
		}
	}
	return selected, nil
}

// JoinPipelines renders a pipeline set as a comma-separated list, for error
// messages and for the line a worker logs at boot.
func JoinPipelines(ps []Pipeline) string {
	names := make([]string, len(ps))
	for i, p := range ps {
		names[i] = string(p)
	}
	return strings.Join(names, ", ")
}
