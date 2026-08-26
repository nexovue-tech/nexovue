package logging

import "strings"

// TraceparentHeader is the W3C Trace Context header carrying the caller's ids.
const TraceparentHeader = "traceparent"

// Shape of a traceparent value: version-traceid-spanid-flags.
const (
	traceparentParts     = 4
	traceparentVersion   = 2
	traceIDLength        = 32
	spanIDLength         = 16
	traceparentFlags     = 2
	invalidVersion       = "ff"
	traceparentSeparator = "-"
)

// ParseTraceparent extracts the trace and span ids from a W3C traceparent
// header. It returns empty fields when the header is absent or malformed —
// an unparsable header is not an error, it just means we start our own trace.
//
// P1-26 replaces this with the OpenTelemetry propagator once the services are
// self-instrumented; the field names it feeds stay the same.
func ParseTraceparent(header string) Fields {
	parts := strings.Split(strings.TrimSpace(header), traceparentSeparator)
	if len(parts) != traceparentParts {
		return Fields{}
	}
	version, traceID, spanID, flags := parts[0], parts[1], parts[2], parts[3]

	valid := isHex(version, traceparentVersion) && version != invalidVersion &&
		isSignificantHex(traceID, traceIDLength) &&
		isSignificantHex(spanID, spanIDLength) &&
		isHex(flags, traceparentFlags)
	if !valid {
		return Fields{}
	}
	return Fields{TraceID: traceID, SpanID: spanID}
}

// isSignificantHex accepts a hex id of the right length that is not all zeros,
// which the spec reserves as "no id".
func isSignificantHex(value string, length int) bool {
	return isHex(value, length) && strings.Trim(value, "0") != ""
}

func isHex(value string, length int) bool {
	if len(value) != length {
		return false
	}
	for _, char := range value {
		isDigit := char >= '0' && char <= '9'
		isLower := char >= 'a' && char <= 'f'
		if !isDigit && !isLower {
			return false
		}
	}
	return true
}
