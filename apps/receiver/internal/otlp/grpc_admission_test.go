package otlp_test

import (
	"compress/gzip"
	"io"
	"strings"
	"sync/atomic"
	"testing"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/encoding"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// countingGzip is gzip that counts how often the server decompresses with it.
// Registered under its own name so it cannot change any other test's gzip.
type countingGzip struct{ decompressed atomic.Int64 }

func (c *countingGzip) Compress(w io.Writer) (io.WriteCloser, error) { return gzip.NewWriter(w), nil }

func (c *countingGzip) Decompress(r io.Reader) (io.Reader, error) {
	c.decompressed.Add(1)
	return gzip.NewReader(r)
}

func (c *countingGzip) Name() string { return "nexovue-test-counting-gzip" }

var serverDecompressions = &countingGzip{}

func init() { encoding.RegisterCompressor(serverDecompressions) }

// bloatedExport is one span whose single attribute is megabytes of one repeated
// byte: kilobytes on the wire, megabytes once decompressed — the shape that
// filled the development receiver (docs/changes/ingest-cannot-be-knocked-over).
func bloatedExport(decodedBytes int) *collectorpb.ExportTraceServiceRequest {
	return &collectorpb.ExportTraceServiceRequest{ResourceSpans: []*tracepb.ResourceSpans{{
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{
			Name: "bloated", TraceId: traceID(0x0b), SpanId: make([]byte, 8),
			Attributes: []*commonpb.KeyValue{{Key: "padding", Value: &commonpb.AnyValue{
				Value: &commonpb.AnyValue_StringValue{StringValue: strings.Repeat("a", decodedBytes)},
			}}},
		}}}},
	}}}
}

func TestGRPCKeylessExportIsRefusedBeforeItIsRead(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{maxBodyBytes: 1 << 20})
	before := serverDecompressions.decompressed.Load()

	_, err := d.client.Export(callContext(t, ""), bloatedExport(3<<20),
		grpc.UseCompressor(serverDecompressions.Name()))

	if got := grpcStatus(t, err).Code(); got != codes.Unauthenticated {
		t.Fatalf("code = %v, want %v", got, codes.Unauthenticated)
	}
	if read := serverDecompressions.decompressed.Load() - before; read != 0 {
		t.Errorf("the server decompressed the body %d time(s) before refusing it: "+
			"a keyless export must be refused before it costs memory", read)
	}
}

func TestGRPCExportBeyondTheUnverifiedShareIsAskedToRetry(t *testing.T) {
	// No room for a key this process has never seen, so the first export from
	// any key is turned away before it is read — with a delay, because it is
	// load, not the caller's fault.
	d := newGRPCDoor(t, doorOptions{admission: otlp.NewAdmission(8, 0)})
	before := serverDecompressions.decompressed.Load()

	_, err := d.client.Export(callContext(t, validKey), bloatedExport(1024),
		grpc.UseCompressor(serverDecompressions.Name()))

	st := grpcStatus(t, err)
	if st.Code() != codes.ResourceExhausted {
		t.Fatalf("code = %v, want %v", st.Code(), codes.ResourceExhausted)
	}
	if _, carried := retryDelay(t, st); !carried {
		t.Error("a load refusal must carry RetryInfo, or OTLP exporters drop the batch")
	}
	if read := serverDecompressions.decompressed.Load() - before; read != 0 {
		t.Errorf("decompressed %d time(s) an export there was no room for", read)
	}
}
