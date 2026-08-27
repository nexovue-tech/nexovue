package otlp

import (
	"errors"
	"mime"
	"net/http"
	"strconv"
	"time"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// The two encodings OTLP/HTTP defines. A server must answer in the same one it
// was asked in, failures included.
const (
	contentTypeProtobuf = "application/x-protobuf"
	contentTypeJSON     = "application/json"
)

const (
	headerContentType     = "Content-Type"
	headerContentEncoding = "Content-Encoding"
	headerRetryAfter      = "Retry-After"
)

var errUnsupportedContentType = errors.New("unsupported content type")

// codec is one of OTLP's two encodings, together with the header that names it.
type codec struct {
	contentType string
	marshal     func(proto.Message) ([]byte, error)
	unmarshal   func([]byte, proto.Message) error
}

var protobufCodec = codec{
	contentType: contentTypeProtobuf,
	marshal:     proto.Marshal,
	unmarshal:   proto.Unmarshal,
}

// DiscardUnknown on the way in: OTLP/JSON senders may speak a newer generation
// of the proto than this build was compiled against, and a field we do not know
// yet is not a reason to reject a batch.
var jsonCodec = codec{
	contentType: contentTypeJSON,
	marshal:     func(m proto.Message) ([]byte, error) { return protojson.Marshal(m) },
	unmarshal: func(b []byte, m proto.Message) error {
		// OTLP/JSON sends ids as hex; protojson reads `bytes` as base64.
		normalized, err := normalizeJSONIDs(b)
		if err != nil {
			return err
		}
		return protojson.UnmarshalOptions{DiscardUnknown: true}.Unmarshal(normalized, m)
	},
}

// codecFor picks the encoding a request speaks.
//
// An absent Content-Type is read as protobuf, OTLP/HTTP's binary default, so a
// hand-rolled client that omits the header still works.
func codecFor(header string) (codec, error) {
	if header == "" {
		return protobufCodec, nil
	}
	mediaType, _, err := mime.ParseMediaType(header)
	if err != nil {
		return codec{}, errUnsupportedContentType
	}
	switch mediaType {
	case contentTypeProtobuf:
		return protobufCodec, nil
	case contentTypeJSON:
		return jsonCodec, nil
	default:
		return codec{}, errUnsupportedContentType
	}
}

// replyCodec is what a failure is written in when the request may not have got
// as far as content negotiation. It never fails: an unreadable Content-Type
// falls back to protobuf rather than leaving the caller with no body at all.
func replyCodec(r *http.Request) codec {
	if chosen, err := codecFor(r.Header.Get(headerContentType)); err == nil {
		return chosen
	}
	return protobufCodec
}

// writeStatus sends an OTLP failure.
//
// The body of every 4xx and 5xx is a google.rpc.Status, not problem+json: SDK
// clients parse the protocol's own error shape and know nothing about the
// conventions our control plane answers with.
func writeStatus(w http.ResponseWriter, enc codec, httpStatus int, rpcCode code.Code, message string) {
	body, err := enc.marshal(&status.Status{Code: int32(rpcCode), Message: message})
	if err != nil {
		// Nothing useful is left to say; the status code still carries the
		// verdict, which is what a client acts on.
		w.WriteHeader(httpStatus)
		return
	}
	w.Header().Set(headerContentType, enc.contentType)
	w.WriteHeader(httpStatus)
	_, _ = w.Write(body)
}

// writeRetryableStatus is writeStatus for the two codes OTLP marks retryable,
// carrying the delay the client should wait.
func writeRetryableStatus(
	w http.ResponseWriter,
	enc codec,
	httpStatus int,
	rpcCode code.Code,
	message string,
	retryAfter time.Duration,
) {
	w.Header().Set(headerRetryAfter, retryAfterSeconds(retryAfter))
	writeStatus(w, enc, httpStatus, rpcCode, message)
}

// retryAfterSeconds renders RFC 9110's delay-seconds form. Zero is not a legal
// answer to "how long should I wait", so anything under a second becomes one.
func retryAfterSeconds(delay time.Duration) string {
	seconds := int64(1)
	if delay > time.Second {
		seconds = int64((delay + time.Second - 1) / time.Second)
	}
	return strconv.FormatInt(seconds, 10)
}
