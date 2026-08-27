package otlp

import (
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// Content encodings the door accepts. gzip is what the OTel SDKs send by
// default, so it is not optional.
const (
	encodingGzip     = "gzip"
	encodingIdentity = "identity"
)

var (
	errBodyTooLarge             = errors.New("request body is larger than the configured limit")
	errUnsupportedContentEncode = errors.New("unsupported content encoding")
	errUnreadableBody           = errors.New("request body could not be read")
)

// bodyLimits bound what one request may cost us.
type bodyLimits struct {
	// wire caps the bytes that arrive.
	wire int64
	// decoded caps what a compressed body may expand to, which is the limit
	// that stops a gzip bomb — the wire limit alone does not.
	decoded int64
}

// readBody returns the decoded request body, refusing anything past the limits.
func readBody(w http.ResponseWriter, r *http.Request, limits bodyLimits) ([]byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, limits.wire)

	encoding := strings.TrimSpace(r.Header.Get(headerContentEncoding))
	switch encoding {
	case "", encodingIdentity:
		return readAll(r.Body, limits.wire)
	case encodingGzip:
		decompressor, err := gzip.NewReader(r.Body)
		if err != nil {
			return nil, fmt.Errorf("%w: %w", errUnreadableBody, err)
		}
		defer func() { _ = decompressor.Close() }()
		return readAll(decompressor, limits.decoded)
	default:
		return nil, errUnsupportedContentEncode
	}
}

// readAll reads up to limit bytes and refuses the request if there is more,
// rather than truncating it into something that would parse as a short batch.
func readAll(source io.Reader, limit int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(source, limit+1))

	var tooLarge *http.MaxBytesError
	switch {
	case errors.As(err, &tooLarge):
		return nil, errBodyTooLarge
	case err != nil:
		return nil, fmt.Errorf("%w: %w", errUnreadableBody, err)
	case int64(len(body)) > limit:
		return nil, errBodyTooLarge
	}
	return body, nil
}
