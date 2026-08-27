package otlp

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
)

// OTLP/JSON encodes trace and span ids as hex, deliberately departing from
// protobuf's JSON mapping, which encodes `bytes` as base64. `protojson` knows
// only the mapping, so every spec-compliant JSON exporter — opentelemetry-js
// over http/json, the collector's otlphttp with encoding: json — is rejected
// unless the ids are converted first.
//
// The two encodings are not safely distinguishable in general: a 32-character
// hex id is also valid base64, and decodes silently to 24 bytes rather than 16.
// Hex is therefore tested first and by exact length, so a well-formed hex id can
// never be mistaken for base64. Anything that is not hex of the right width is
// left untouched, which keeps a base64-sending client working.
var idByteWidths = map[string]int{
	"traceId":        16,
	"trace_id":       16,
	"spanId":         8,
	"span_id":        8,
	"parentSpanId":   8,
	"parent_span_id": 8,
}

// normalizeJSONIDs rewrites hex trace and span ids into the base64 protojson
// expects, anywhere they appear — spans, and the links that reference them.
func normalizeJSONIDs(raw []byte) ([]byte, error) {
	var document any
	if err := json.Unmarshal(raw, &document); err != nil {
		// Not JSON at all. Hand it back and let protojson produce the error,
		// so there is one message for a malformed body rather than two.
		return raw, nil //nolint:nilerr // the decoder below reports it properly
	}
	rewriteIDs(document)
	return json.Marshal(document)
}

func rewriteIDs(node any) {
	switch value := node.(type) {
	case map[string]any:
		for key, child := range value {
			if width, carriesID := idByteWidths[key]; carriesID {
				if encoded, ok := child.(string); ok {
					if converted, converted_ok := hexToBase64(encoded, width); converted_ok {
						value[key] = converted
					}
				}
				continue
			}
			rewriteIDs(child)
		}
	case []any:
		for _, child := range value {
			rewriteIDs(child)
		}
	}
}

// hexToBase64 converts an id only when it is unambiguously hex of the exact
// width the field requires. An all-zero id is a valid encoding of "absent" and
// converts like any other.
func hexToBase64(encoded string, byteWidth int) (string, bool) {
	if len(encoded) != byteWidth*2 {
		return "", false
	}
	decoded, err := hex.DecodeString(encoded)
	if err != nil {
		return "", false
	}
	return base64.StdEncoding.EncodeToString(decoded), true
}
