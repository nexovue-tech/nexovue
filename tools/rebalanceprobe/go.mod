module github.com/nexovue-tech/nexovue/tools/rebalanceprobe

go 1.27.3

// packages/contracts is a workspace member, never published. The replace keeps
// `go mod tidy` and any non-workspace build resolving it from the repo.
replace github.com/nexovue-tech/nexovue/packages/contracts => ../../packages/contracts

require (
	github.com/google/uuid v1.6.0
	github.com/nexovue-tech/nexovue/packages/contracts v0.0.0-00010101000000-000000000000
	github.com/twmb/franz-go v1.22.0
	go.opentelemetry.io/proto/otlp v1.11.0
	golang.org/x/sync v0.23.0
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/grpc-ecosystem/grpc-gateway/v2 v2.29.0 // indirect
	github.com/klauspost/compress v1.20.0 // indirect
	github.com/pierrec/lz4/v4 v4.1.30 // indirect
	github.com/twmb/franz-go/pkg/kmsg v1.14.0 // indirect
	go.opentelemetry.io/otel/metric v1.45.0 // indirect
	go.opentelemetry.io/otel/sdk v1.45.0 // indirect
	go.opentelemetry.io/otel/trace v1.45.0 // indirect
	golang.org/x/net v0.60.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	golang.org/x/text v0.42.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20260803160001-6ac0973c030d // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260825221802-da73d73af1c5 // indirect
	google.golang.org/grpc v1.83.2 // indirect
)
