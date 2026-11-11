// Package proberdoor decides which telemetry may claim to be a synthetic check's.
//
// The check screens read a check's readings from `metrics_gauge` by metric name
// and its evidence from `logs` by the resource's `synthetic.check.*` attributes,
// and nothing else marks them as the prober's. Only the prober stamps
// DOOR_SYNTHETIC on its envelopes, so that door is the only one trusted to write
// them: a site key is printed in its page, and a secret key belongs to the
// customer's own servers. Without this, either could make a down check read up
// (docs/changes/browser-door). The metric and log pipelines both ask here, so
// the two cannot disagree about what a claim is.
package proberdoor

import (
	"log/slog"
	"strings"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// MetricPrefix starts every metric the prober publishes (`synthetic.check.*`,
// `synthetic.tls.*`; nexovue_scheduler/publish.py).
const MetricPrefix = "synthetic."

// The attribute path the check screens find a check under, and its first step.
const (
	checkPath     = "synthetic.check"
	checkRootStep = "synthetic"
)

// Trusted reports whether a door may write a synthetic check's readings and
// evidence.
func Trusted(door nexovuev1.Door) bool { return door == nexovuev1.Door_DOOR_SYNTHETIC }

// ClaimsMetric reports whether a metric name is one the check screens read as a
// check's reading.
func ClaimsMetric(name string) bool { return strings.HasPrefix(name, MetricPrefix) }

// ClaimsCheck reports whether resource attributes would be stored under
// `synthetic.check`, which is how the check screens find a check's rows.
//
// It asks the stored shape rather than the keys: a dot is a path separator to
// ClickHouse, so a key `synthetic` holding a map with `check` inside reaches the
// same subcolumn as `synthetic.check.id` does (otlpvalue.AttributesMap). Only an
// attribute whose first step is `synthetic` can, so the rest are not rendered.
func ClaimsCheck(attributes []*commonpb.KeyValue) bool {
	rooted := false
	for _, attribute := range attributes {
		if first, _, _ := strings.Cut(attribute.GetKey(), "."); first == checkRootStep {
			rooted = true
			break
		}
	}
	return rooted && otlpvalue.AttributeAt(otlpvalue.AttributesMap(attributes), checkPath) != nil
}

// Refusals is what a message or a batch dropped as a forged claim, naming the
// sender of the first: the log line it becomes is a pointer to go and look, not
// a census.
type Refusals struct {
	Count     int
	TenantID  string
	ProjectID string
	Door      nexovuev1.Door
}

// Note records count refusals from one envelope.
func (r *Refusals) Note(count int, envelope *nexovuev1.Envelope) {
	r.Merge(Refusals{
		Count:     count,
		TenantID:  envelope.GetTenantId(),
		ProjectID: envelope.GetProjectId(),
		Door:      envelope.GetDoor(),
	})
}

// Merge folds another set into this one, keeping the first sender named.
func (r *Refusals) Merge(other Refusals) {
	if other.Count <= 0 {
		return
	}
	if r.Count == 0 {
		*r = other
		return
	}
	r.Count += other.Count
}

// Attrs are the log fields that say what was dropped and from whom.
func (r Refusals) Attrs() []slog.Attr {
	return []slog.Attr{
		slog.Int("refused", r.Count),
		slog.String("tenant_id", r.TenantID),
		slog.String("project_id", r.ProjectID),
		slog.String("door", r.Door.String()),
	}
}
