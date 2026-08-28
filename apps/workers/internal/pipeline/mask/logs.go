package mask

import (
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
)

// MaskResourceLogs masks every value the project's rules cover, across the
// resource, its log records' attributes and their bodies. It returns how many
// values it replaced, which is what the pipeline logs and what a test asserts on.
//
// Like the span path it rewrites the decoded payload in place, so the rows the
// log writer builds afterwards can only be built from masked values.
func (m *Masker) MaskResourceLogs(projectID string, batches []*logspb.ResourceLogs) int {
	rules := m.rules.MaskingRules(projectID)
	masked := 0
	for _, batch := range batches {
		if batch == nil {
			continue
		}
		masked += maskAttributes(rules, batch.GetResource().GetAttributes())
		for _, scope := range batch.GetScopeLogs() {
			for _, record := range scope.GetLogRecords() {
				masked += maskLogRecord(rules, record)
			}
		}
	}
	return masked
}

// maskLogRecord covers the two places a record carries caller-controlled text:
// its attributes, and its body.
//
// The body is an AnyValue, so it goes through the same walk as a structured
// attribute: a map body's keys meet the key rules exactly as attribute keys do,
// and a rendered string body is scanned for matched runs.
func maskLogRecord(rules Rules, record *logspb.LogRecord) int {
	if record == nil {
		return 0
	}
	return maskAttributes(rules, record.GetAttributes()) +
		maskValueTree(rules, record.GetBody())
}
