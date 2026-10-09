package otlp

import (
	"context"
	"net"
	"time"

	"google.golang.org/grpc"
	// Registers gzip as a decodable grpc-encoding, nothing more: the import
	// has no exported symbol because codecs self-register in encoding's global
	// map on init. Without it a Collector-side gRPC exporter that compresses —
	// several do by default — is refused `Unimplemented: Decompressor is not
	// installed for grpc-encoding "gzip"` before any gate in this package ever
	// runs. MaxRecvMsgSize below already assumed this: its own comment states
	// the cap is sized post-decompression, which only matters once decoding a
	// compressed message is actually possible.
	_ "google.golang.org/grpc/encoding/gzip"
	"google.golang.org/grpc/keepalive"
)

// Keepalive policy for an ingest endpoint, which is not the shape grpc-go's
// defaults assume. An exporter holds one connection open and is idle between
// batches, so the defaults — a ping no more often than every five minutes, and
// none at all without an active stream — will GOAWAY a perfectly well-behaved
// collector with `too_many_pings`. Customers running their own collector in
// front of us are onboarding pattern B, so this is a path we have to keep open.
const (
	// How often a client may ping. More permissive than grpc-go's five minutes:
	// we *want* clients keeping the connection warm rather than reconnecting.
	minClientPingInterval = 30 * time.Second

	// Idle connections may ping. Between batches there is no active stream, and
	// refusing pings then is refusing them exactly when they are the point.
	permitPingWithoutStream = true

	// Force a reconnect periodically so a load balancer can rebalance; without
	// it a long-lived exporter pins itself to one receiver instance forever.
	maxConnectionAge      = 30 * time.Minute
	maxConnectionAgeGrace = 5 * time.Minute
)

// GRPCServer is the OTLP/gRPC transport, serving all four collector services.
// Its lifecycle mirrors *http.Server's so the process starts and drains every
// listener the same way.
type GRPCServer struct {
	addr   string
	server *grpc.Server
}

// NewGRPCServer returns the OTLP/gRPC listener bound to addr, running the same
// gate chain the HTTP doors run.
func NewGRPCServer(addr string, opts Options) *GRPCServer {
	server := grpc.NewServer(
		// Stated rather than inherited: grpc-go's own default is 4 MiB, and it
		// applies the limit to the message both on the wire and again after
		// decompression. The decoded cap is therefore the one to use — it is
		// what bounds the memory a single export can cost us, and matching the
		// wire cap instead would refuse compressed batches the HTTP door takes.
		//
		// grpc-go enforces this before any handler runs, so an export past it
		// is refused RESOURCE_EXHAUSTED where the HTTP door answers 413.
		grpc.MaxRecvMsgSize(int(opts.MaxDecodedBodyBytes)),
		grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
			MinTime:             minClientPingInterval,
			PermitWithoutStream: permitPingWithoutStream,
		}),
		grpc.KeepaliveParams(keepalive.ServerParameters{
			MaxConnectionAge:      maxConnectionAge,
			MaxConnectionAgeGrace: maxConnectionAgeGrace,
		}),
	)

	registerExporters(server, opts)
	return &GRPCServer{addr: addr, server: server}
}

// Addr is the address the door listens on.
func (s *GRPCServer) Addr() string { return s.addr }

// ListenAndServe binds and serves until Shutdown, returning nil once it has.
// The bind happens here rather than in the constructor so a port already in use
// surfaces the same way the HTTP door's does: as the error that stops the boot.
func (s *GRPCServer) ListenAndServe() error {
	listener, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	return s.Serve(listener)
}

// Serve serves on an already-bound listener.
func (s *GRPCServer) Serve(listener net.Listener) error {
	return s.server.Serve(listener)
}

// Shutdown stops accepting and waits for in-flight exports, giving up when ctx
// expires. gRPC has no context-aware stop — GracefulStop blocks until the last
// call returns — so the budget is enforced from outside by the hard Stop.
func (s *GRPCServer) Shutdown(ctx context.Context) error {
	drained := make(chan struct{})
	go func() {
		s.server.GracefulStop()
		close(drained)
	}()

	select {
	case <-drained:
		return nil
	case <-ctx.Done():
		s.server.Stop()
		<-drained
		return ctx.Err()
	}
}
