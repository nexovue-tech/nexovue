// Package semconv reads the OpenTelemetry convention generation a payload
// declares out of the schema URL it arrived with.
//
// Every door records it in the envelope's semconv_version, because the HTTP
// conventions stabilized with 17 attribute renames and normalization downstream
// reads the field rather than assuming a generation.
package semconv

import "strings"

// FromSchemaURLs returns the version the first URL carrying one declares, and
// empty when none does.
//
// Callers pass the most specific URL first: a scope's schema describes the
// telemetry, a resource's only its attributes.
func FromSchemaURLs(schemaURLs ...string) string {
	for _, schemaURL := range schemaURLs {
		if schemaURL == "" {
			continue
		}
		// A schema URL ends in its version: http[s]://server[:port]/path/<version>.
		if version := schemaURL[strings.LastIndex(schemaURL, "/")+1:]; version != "" {
			return version
		}
	}
	return ""
}
