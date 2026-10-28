module github.com/nexovue-tech/nexovue/tools/rebalanceprobe

go 1.27.0

// packages/contracts is a workspace member, never published. The replace keeps
// `go mod tidy` and any non-workspace build resolving it from the repo.
replace github.com/nexovue-tech/nexovue/packages/contracts => ../../packages/contracts

require (
	github.com/google/uuid v1.6.0
	github.com/nexovue-tech/nexovue/packages/contracts v0.0.0-00010101000000-000000000000
	github.com/twmb/franz-go v1.22.2
	go.opentelemetry.io/proto/otlp v1.11.1
	golang.org/x/sync v0.23.0
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/grpc-ecosystem/grpc-gateway/v2 v2.31.0 // indirect
	github.com/klauspost/compress v1.20.3 // indirect
	github.com/pierrec/lz4/v4 v4.1.33 // indirect
	github.com/twmb/franz-go/pkg/kmsg v1.15.0 // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	golang.org/x/text v0.42.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20260928230214-8a89bd6388cc // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260928230214-8a89bd6388cc // indirect
	google.golang.org/grpc v1.84.0 // indirect
)
