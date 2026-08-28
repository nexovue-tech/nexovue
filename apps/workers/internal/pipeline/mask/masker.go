package mask

import (
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

// RulesSource supplies the ruleset for a project. An interface because the
// rules live in Postgres in production and in a test table here, and because
// the masking logic must be testable without a database.
type RulesSource interface {
	// MaskingRules returns the rules for a project, which for a project with
	// no configuration of its own are the defaults.
	MaskingRules(projectID string) Rules
}

// StaticRules is a RulesSource that answers with one ruleset for every project.
// It is what a worker runs on when no control-plane connection is configured.
type StaticRules Rules

// MaskingRules implements RulesSource.
func (s StaticRules) MaskingRules(string) Rules { return Rules(s) }

// Masker applies a project's rules to decoded spans, in place.
//
// In place is the point: the caller holds the only copy of the decoded payload,
// so once this returns there is no unmasked version left anywhere for a later
// stage to reach for.
type Masker struct {
	rules RulesSource
}

// New returns a masker reading rules from source.
func New(source RulesSource) *Masker {
	return &Masker{rules: source}
}

// MaskResourceSpans masks every attribute the project's rules cover, across the
// resource, its spans, their events and their status messages. It returns how
// many values it replaced, which is what the pipeline logs and what a test
// asserts on.
func (m *Masker) MaskResourceSpans(projectID string, batches []*tracepb.ResourceSpans) int {
	rules := m.rules.MaskingRules(projectID)
	masked := 0
	for _, batch := range batches {
		if batch == nil {
			continue
		}
		masked += maskAttributes(rules, batch.GetResource().GetAttributes())
		for _, scope := range batch.GetScopeSpans() {
			for _, span := range scope.GetSpans() {
				masked += maskSpan(rules, span)
			}
		}
	}
	return masked
}

func maskSpan(rules Rules, span *tracepb.Span) int {
	if span == nil {
		return 0
	}
	masked := maskAttributes(rules, span.GetAttributes())
	for _, event := range span.GetEvents() {
		masked += maskAttributes(rules, event.GetAttributes())
	}
	for _, link := range span.GetLinks() {
		masked += maskAttributes(rules, link.GetAttributes())
	}

	// The status message is free text an SDK copies from an exception, which
	// is one of the likelier places a card number ends up.
	if status := span.GetStatus(); status != nil && rules.masksValues() {
		if replaced, changed := rules.maskValue(status.GetMessage()); changed {
			status.Message = replaced
			masked++
		}
	}
	return masked
}

// maskAttributes rewrites the values of attributes the rules cover. A masked
// key loses its value entirely; an unmasked key keeps its value with any
// matched run inside it replaced.
func maskAttributes(rules Rules, attributes []*commonpb.KeyValue) int {
	masked := 0
	for _, attribute := range attributes {
		if attribute == nil || attribute.GetKey() == "" {
			continue
		}
		// The allowlist is checked before any rule: a value the project named
		// as safe survives however broadly it matches.
		if rules.allows(attribute.GetValue().GetStringValue()) {
			continue
		}
		if act, ok := rules.masksKey(attribute.GetKey()); ok {
			attribute.Value = maskedValue(rules.replace(act, attribute.GetValue().GetStringValue()))
			masked++
			continue
		}
		// Descent is unconditional: the keys inside a structured value are
		// subject to the key rules too, so a project that switched the value
		// rules off still gets a nested `password` masked.
		masked += maskValueTree(rules, attribute.Value)
	}
	return masked
}

// maskValueTree scans a value for matched runs, descending into arrays and
// key-value lists so neither a card number nor a credential key nested in a
// structured value is missed.
func maskValueTree(rules Rules, value *commonpb.AnyValue) int {
	if value == nil {
		return 0
	}
	switch typed := value.GetValue().(type) {
	case *commonpb.AnyValue_StringValue:
		replaced, changed := rules.maskValue(typed.StringValue)
		if !changed {
			return 0
		}
		typed.StringValue = replaced
		return 1
	case *commonpb.AnyValue_ArrayValue:
		masked := 0
		for _, element := range typed.ArrayValue.GetValues() {
			masked += maskValueTree(rules, element)
		}
		return masked
	case *commonpb.AnyValue_KvlistValue:
		return maskAttributes(rules, typed.KvlistValue.GetValues())
	default:
		return 0
	}
}

func maskedValue(replacement string) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: replacement}}
}
