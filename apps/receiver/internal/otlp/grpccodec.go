package otlp

import (
	"google.golang.org/grpc/encoding"
	"google.golang.org/grpc/encoding/proto"
	"google.golang.org/grpc/mem"
)

// exportCodec is grpc-go's own protobuf codec, except that an export request it cannot decode is
// handed to the handler instead of answered by grpc-go. Since grpc-go 1.84 a unary call is served
// as a stream, and a failed decode makes RecvMsg write INTERNAL to the client before the handler
// returns; OTLP's answer for a payload the server cannot read is INVALID_ARGUMENT, not retryable.
type exportCodec struct {
	encoding.CodecV2
}

// exportDecoding carries the request to decode into, and how decoding went.
type exportDecoding struct {
	into any
	err  error
}

func newExportCodec() exportCodec {
	return exportCodec{CodecV2: encoding.GetCodecV2(proto.Name)}
}

// Unmarshal decodes as grpc-go would; for an export it reports the failure to the handler and
// nothing to grpc-go.
func (c exportCodec) Unmarshal(data mem.BufferSlice, v any) error {
	decoding, isExport := v.(*exportDecoding)
	if !isExport {
		return c.CodecV2.Unmarshal(data, v)
	}
	decoding.err = c.CodecV2.Unmarshal(data, decoding.into)
	return nil
}
