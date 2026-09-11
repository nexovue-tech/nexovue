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

	// contentTypeText is not an OTLP encoding. It is the CORS safelist's, and
	// the browser door alone reads it as OTLP/JSON — see browserContentTypes
	// below.
	contentTypeText = "text/plain"
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

// contentTypes is the set of request encodings one door reads, keyed by media
// type. A door is built with one set and cannot be talked into another by a
// request, which is the same reason each door has one gatekeeper: an affordance
// that exists for one entrance should be absent from the other's code, not
// guarded by a condition somebody has to remember.
type contentTypes map[string]codec

// serverContentTypes is OTLP/HTTP's own pair, and nothing else. There is no
// text/plain entry here, so no request on a server path can reach the JSON
// codec through one.
var serverContentTypes = contentTypes{
	contentTypeProtobuf: protobufCodec,
	contentTypeJSON:     jsonCodec,
}

// browserContentTypes adds text/plain, read as OTLP/JSON.
//
// navigator.sendBeacon is the only export that survives a page unload, it can
// set no headers, and it fixes the request's credentials mode to "include" — so
// a body typed application/json is preflighted, and the POST behind the
// preflight is then failed by the browser because we deliberately never send
// Access-Control-Allow-Credentials. text/plain is CORS-safelisted,
// so the beacon is delivered instead of refused. The body is still parsed as
// JSON and a body that is not JSON is still refused.
var browserContentTypes = contentTypes{
	contentTypeProtobuf: protobufCodec,
	contentTypeJSON:     jsonCodec,
	contentTypeText:     jsonCodec,
}

// codecFor picks the encoding a request speaks, out of what this door reads.
//
// An absent Content-Type is read as protobuf, OTLP/HTTP's binary default, so a
// hand-rolled client that omits the header still works.
func (accepted contentTypes) codecFor(header string) (codec, error) {
	if header == "" {
		return protobufCodec, nil
	}
	mediaType, _, err := mime.ParseMediaType(header)
	if err != nil {
		return codec{}, errUnsupportedContentType
	}
	chosen, ok := accepted[mediaType]
	if !ok {
		return codec{}, errUnsupportedContentType
	}
	return chosen, nil
}

// replyCodec is what a failure is written in when the request may not have got
// as far as content negotiation. It never fails: an unreadable Content-Type
// falls back to protobuf rather than leaving the caller with no body at all.
//
// A text/plain export is therefore answered in application/json, which is what
// its body was: the content type a beacon must use is the CORS safelist's
// requirement, not a claim about the encoding.
func (accepted contentTypes) replyCodec(r *http.Request) codec {
	if chosen, err := accepted.codecFor(r.Header.Get(headerContentType)); err == nil {
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

// writeRefusal renders a gate's verdict as OTLP/HTTP, adding Retry-After when
// waiting is worth the client's while.
func writeRefusal(w http.ResponseWriter, enc codec, refused *refusal) {
	if refused.retryAfter > 0 {
		w.Header().Set(headerRetryAfter, retryAfterSeconds(refused.retryAfter))
	}
	writeStatus(w, enc, refused.status(), refused.code, refused.message)
}

// httpStatusFor is OTLP/HTTP's pairing of an rpc code with a status line. Only
// the codes the gates can produce are listed; anything else is our bug, and 500
// is what a bug means.
func httpStatusFor(rpcCode code.Code) int {
	switch rpcCode {
	case code.Code_UNAUTHENTICATED:
		return http.StatusUnauthorized
	case code.Code_PERMISSION_DENIED:
		return http.StatusForbidden
	case code.Code_RESOURCE_EXHAUSTED:
		return http.StatusTooManyRequests
	case code.Code_INVALID_ARGUMENT:
		return http.StatusBadRequest
	case code.Code_UNAVAILABLE:
		return http.StatusServiceUnavailable
	default:
		return http.StatusInternalServerError
	}
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
