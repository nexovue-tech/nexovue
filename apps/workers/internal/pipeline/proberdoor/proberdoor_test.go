package proberdoor

import (
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

func text(value string) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}}
}

func kvlist(values ...*commonpb.KeyValue) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{
		KvlistValue: &commonpb.KeyValueList{Values: values},
	}}
}

// Only the prober's own door is trusted with a check's telemetry.
func TestOnlyTheProbersDoorIsTrusted(t *testing.T) {
	for door, want := range map[nexovuev1.Door]bool{
		nexovuev1.Door_DOOR_SYNTHETIC:   true,
		nexovuev1.Door_DOOR_RUM:         false,
		nexovuev1.Door_DOOR_OTLP:        false,
		nexovuev1.Door_DOOR_POLLER:      false,
		nexovuev1.Door_DOOR_UNSPECIFIED: false,
	} {
		if got := Trusted(door); got != want {
			t.Errorf("Trusted(%s) = %v, want %v", door, got, want)
		}
	}
}

// Every name the prober publishes is a claim; a lookalike that is not under the
// prefix is not.
func TestTheProbersMetricNamesAreClaims(t *testing.T) {
	for name, want := range map[string]bool{
		"synthetic.check.status":              true,
		"synthetic.check.phase.duration":      true,
		"synthetic.tls.certificate.remaining": true,
		"synthetics.check.status":             false,
		"page.synthetic.check":                false,
		"http.server.duration":                false,
	} {
		if got := ClaimsMetric(name); got != want {
			t.Errorf("ClaimsMetric(%q) = %v, want %v", name, got, want)
		}
	}
}

// A resource is judged by where its attributes would be stored, so nesting a
// map under `synthetic` is the same claim as the dotted key.
func TestAResourceClaimsACheckByWhereItWouldBeStored(t *testing.T) {
	cases := map[string]struct {
		attributes []*commonpb.KeyValue
		want       bool
	}{
		"the prober's dotted key": {
			[]*commonpb.KeyValue{{Key: "synthetic.check.id", Value: text("c1")}}, true,
		},
		"a map under synthetic": {
			[]*commonpb.KeyValue{{Key: "synthetic", Value: kvlist(
				&commonpb.KeyValue{Key: "check", Value: kvlist(&commonpb.KeyValue{Key: "id", Value: text("c1")})},
			)}}, true,
		},
		"a map under synthetic.check": {
			[]*commonpb.KeyValue{{Key: "synthetic.check", Value: kvlist(&commonpb.KeyValue{Key: "id", Value: text("c1")})}}, true,
		},
		"another synthetic attribute": {
			[]*commonpb.KeyValue{{Key: "synthetic.source", Value: text("lab")}}, false,
		},
		"an ordinary resource": {
			[]*commonpb.KeyValue{{Key: "service.name", Value: text("checkout")}}, false,
		},
		"no attributes": {nil, false},
	}
	for name, tc := range cases {
		if got := ClaimsCheck(tc.attributes); got != tc.want {
			t.Errorf("%s: ClaimsCheck = %v, want %v", name, got, tc.want)
		}
	}
}
