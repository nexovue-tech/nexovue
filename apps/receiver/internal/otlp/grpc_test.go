package otlp_test

import (
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// The gRPC door runs over an in-memory listener: the transport is the real one,
// including framing and metadata, without needing a port.
const bufconnBufferSize = 1 << 20

// exportMethod is the full method name, which a raw call has to name itself.
const exportMethod = "/opentelemetry.proto.collector.trace.v1.TraceService/Export"

// --- fixtures ----------------------------------------------------------------

type grpcDoor struct {
	client   collectorpb.TraceServiceClient
	conn     *grpc.ClientConn
	producer *countingProducer
}

func newGRPCDoor(t *testing.T, opts doorOptions) grpcDoor {
	t.Helper()
	opts.applyDefaults()

	producer := &countingProducer{err: opts.produceErr}
	door := serverDoorOptions(producer, opts)
	screen, screens := door.Gatekeeper.(otlp.Screener)
	if !screens {
		t.Fatal("the server door's gatekeeper cannot screen a key before the body is read")
	}
	server := otlp.NewGRPCServer("bufconn", door, screen)

	listener := bufconn.Listen(bufconnBufferSize)
	served := make(chan error, 1)
	go func() { served <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Shutdown(context.Background()); err != nil {
			t.Errorf("draining the gRPC door: %v", err)
		}
		if err := <-served; err != nil {
			t.Errorf("serving the gRPC door: %v", err)
		}
	})

	conn, err := grpc.NewClient("passthrough:///bufconn",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return listener.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dialing the gRPC door: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	return grpcDoor{client: collectorpb.NewTraceServiceClient(conn), conn: conn, producer: producer}
}

// callContext carries the ingest key the way an SDK does: metadata, not headers.
func callContext(t *testing.T, key string) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	t.Cleanup(cancel)
	if key == "" {
		return ctx
	}
	return metadata.AppendToOutgoingContext(ctx, auth.HeaderName, key)
}

// export sends a batch with the dev key.
func (d grpcDoor) export(
	t *testing.T,
	request *collectorpb.ExportTraceServiceRequest,
) (*collectorpb.ExportTraceServiceResponse, error) {
	t.Helper()
	return d.client.Export(callContext(t, validKey), request)
}

// rawCodec puts the exact bytes a test hands it on the wire, so a payload the
// server cannot decode can be sent the way a broken client would send it. It
// answers to "proto" so the server still decodes with its own codec.
type rawCodec struct{}

func (rawCodec) Name() string { return "proto" }

func (rawCodec) Marshal(v any) ([]byte, error) {
	raw, ok := v.([]byte)
	if !ok {
		return nil, fmt.Errorf("rawCodec cannot marshal %T", v)
	}
	return raw, nil
}

func (rawCodec) Unmarshal(data []byte, v any) error {
	target, ok := v.(*[]byte)
	if !ok {
		return fmt.Errorf("rawCodec cannot unmarshal into %T", v)
	}
	*target = data
	return nil
}

// invokeRaw sends bytes one named method has to make sense of on its own.
func (d grpcDoor) invokeRaw(t *testing.T, method string, payload []byte) error {
	t.Helper()
	var reply []byte
	return d.conn.Invoke(callContext(t, validKey), method, payload, &reply,
		grpc.ForceCodec(rawCodec{}))
}

// grpcStatus decodes the status a refused export came back with.
func grpcStatus(t *testing.T, err error) *status.Status {
	t.Helper()
	if err == nil {
		t.Fatal("export succeeded; want a refusal")
	}
	decoded, ok := status.FromError(err)
	if !ok {
		t.Fatalf("failure is not a gRPC status: %v", err)
	}
	return decoded
}

// retryDelay returns the RetryInfo a retryable status must carry.
func retryDelay(t *testing.T, decoded *status.Status) (time.Duration, bool) {
	t.Helper()
	for _, detail := range decoded.Details() {
		if info, ok := detail.(*errdetails.RetryInfo); ok {
			return info.GetRetryDelay().AsDuration(), true
		}
	}
	return 0, false
}

// --- the happy path ----------------------------------------------------------

func TestGRPCBatchIsSplitOneEnvelopePerTrace(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})
	first, second := traceID(0x11), traceID(0x22)

	response, err := d.export(t, exportRequest(first, second, first))
	if err != nil {
		t.Fatalf("export: %v", err)
	}

	if response.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset: everything was accepted",
			response.GetPartialSuccess())
	}
	if len(d.producer.records) != 2 {
		t.Fatalf("produced %d records, want one per trace id", len(d.producer.records))
	}

	keyed := map[string]bool{}
	for _, record := range d.producer.records {
		keyed[string(record.Key)] = true
		if record.Topic != spansTopic {
			t.Errorf("topic = %q, want %q", record.Topic, spansTopic)
		}
		var envelope nexovuev1.Envelope
		if err := proto.Unmarshal(record.Value, &envelope); err != nil {
			t.Fatalf("record is not an Envelope: %v", err)
		}
		if envelope.GetTenantId() != orgID || envelope.GetProjectId() != projectID {
			t.Errorf("envelope tenancy = %s/%s, want %s/%s",
				envelope.GetTenantId(), envelope.GetProjectId(), orgID, projectID)
		}
		if envelope.GetDoor() != nexovuev1.Door_DOOR_OTLP {
			t.Errorf("door = %v, want DOOR_OTLP: gRPC is a transport, not a door",
				envelope.GetDoor())
		}
	}

	want := map[string]bool{
		"11111111111111111111111111111111": true,
		"22222222222222222222222222222222": true,
	}
	for traceIDHex := range want {
		if !keyed[traceIDHex] {
			t.Errorf("no record keyed by trace id %s; keys were %v", traceIDHex, keyed)
		}
	}
	if len(keyed) != len(want) {
		t.Errorf("keys = %v, want exactly %v", keyed, want)
	}
}

func TestGRPCBatchWithNoSpansIsAcceptedAndPublishesNothing(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})

	if _, err := d.export(t, &collectorpb.ExportTraceServiceRequest{}); err != nil {
		t.Fatalf("export: %v", err)
	}
	if len(d.producer.records) != 0 {
		t.Errorf("produced %d records for a batch with no spans", len(d.producer.records))
	}
}

// --- failures ----------------------------------------------------------------

func TestGRPCUnusableCredentialIsRefusedWithoutReachingTheBroker(t *testing.T) {
	cases := map[string]string{
		"no metadata at all": "",
		"malformed":          "not-a-nexovue-key",
		"unknown":            "nvk_live_absentabsentabsentabse",
	}
	for name, key := range cases {
		t.Run(name, func(t *testing.T) {
			d := newGRPCDoor(t, doorOptions{})

			_, err := d.client.Export(callContext(t, key), exportRequest(traceID(0x11)))

			if got := grpcStatus(t, err).Code(); got != codes.Unauthenticated {
				t.Errorf("code = %v, want %v", got, codes.Unauthenticated)
			}
			if len(d.producer.records) != 0 {
				t.Error("an unauthenticated export reached the broker")
			}
		})
	}
}

// A credential must never come back in a status message.
func TestGRPCFailuresNeverEchoTheKey(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})

	_, err := d.export(t, exportRequest(make([]byte, 8)))

	if message := grpcStatus(t, err).Message(); message == "" {
		t.Fatal("a refusal must say why")
	} else if message == validKey {
		t.Error("the status message echoed the ingest key")
	}
}

// An unreachable control plane is our failure, and unlike a rejection it is
// worth retrying.
func TestGRPCUnreachableControlPlaneIsRetryable(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{keys: unreachableKeys{}})

	_, err := d.export(t, exportRequest(traceID(0x11)))

	decoded := grpcStatus(t, err)
	if decoded.Code() != codes.Unavailable {
		t.Fatalf("code = %v, want %v", decoded.Code(), codes.Unavailable)
	}
	if _, carried := retryDelay(t, decoded); !carried {
		t.Error("a retryable status must carry RetryInfo")
	}
}

func TestGRPCOverLimitIsRefusedWithRetryInfo(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{counter: &countingCounter{seen: 5}, requestsPerSec: 1})

	_, err := d.export(t, exportRequest(traceID(0x11)))

	decoded := grpcStatus(t, err)
	if decoded.Code() != codes.ResourceExhausted {
		t.Fatalf("code = %v, want %v", decoded.Code(), codes.ResourceExhausted)
	}
	delay, carried := retryDelay(t, decoded)
	if !carried {
		t.Fatal("a throttled status must tell the client how long to wait")
	}
	if delay <= 0 || delay > time.Second {
		t.Errorf("retry delay = %s, want a wait inside the one-second window", delay)
	}
	if len(d.producer.records) != 0 {
		t.Error("a throttled export reached the broker")
	}
}

// A Valkey outage must not become an ingest outage.
func TestGRPCUnavailableLimiterFailsOpen(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{counter: brokenCounter{}})

	if _, err := d.export(t, exportRequest(traceID(0x11))); err != nil {
		t.Fatalf("export: %v, want telemetry to survive a limiter blip", err)
	}
	if len(d.producer.records) != 1 {
		t.Errorf("produced %d records, want 1", len(d.producer.records))
	}
}

// Bytes that are not an ExportTraceServiceRequest are the caller's to fix, so
// the answer must be one an SDK will not retry.
func TestGRPCMalformedPayloadIsNotRetryable(t *testing.T) {
	cases := map[string][]byte{
		"not protobuf at all": []byte("this is not protobuf at all"),
		// Field 1, wire type 2, declaring 127 bytes that are not there.
		"truncated message": {0x0a, 0x7f},
	}
	for name, payload := range cases {
		t.Run(name, func(t *testing.T) {
			d := newGRPCDoor(t, doorOptions{})

			err := d.invokeRaw(t, exportMethod, payload)

			if got := grpcStatus(t, err).Code(); got != codes.InvalidArgument {
				t.Errorf("code = %v, want %v", got, codes.InvalidArgument)
			}
			if len(d.producer.records) != 0 {
				t.Error("a malformed payload reached the broker")
			}
		})
	}
}

func TestGRPCSpanWithAnUnroutableTraceIDIsRefused(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})

	_, err := d.export(t, exportRequest(make([]byte, 8))) // not 16 bytes

	if got := grpcStatus(t, err).Code(); got != codes.InvalidArgument {
		t.Errorf("code = %v, want %v", got, codes.InvalidArgument)
	}
	if len(d.producer.records) != 0 {
		t.Error("a span that cannot be partitioned reached the broker")
	}
}

// The receive limit is stated, not inherited from grpc-go's 4 MiB. grpc-go
// enforces it before any handler runs, so the refusal is its RESOURCE_EXHAUSTED
// rather than the INVALID_ARGUMENT the HTTP door's 413 carries.
func TestGRPCOversizedExportIsRefused(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{maxBodyBytes: 512})

	err := d.invokeRaw(t, exportMethod, make([]byte, 4096)) // past 512 * 4

	if got := grpcStatus(t, err).Code(); got != codes.ResourceExhausted {
		t.Errorf("code = %v, want %v", got, codes.ResourceExhausted)
	}
	if len(d.producer.records) != 0 {
		t.Error("an oversized export reached the broker")
	}
}

func TestGRPCBrokerRefusalIsReportedAsRetryable(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{produceErr: errors.New("kafka: not leader for partition")})

	_, err := d.export(t, exportRequest(traceID(0x11)))

	decoded := grpcStatus(t, err)
	if decoded.Code() != codes.Unavailable {
		t.Fatalf("code = %v, want %v: the export must not be acked", decoded.Code(), codes.Unavailable)
	}
	if _, carried := retryDelay(t, decoded); !carried {
		t.Error("a retryable status must carry RetryInfo")
	}
}
