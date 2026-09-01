module github.com/nexovue-tech/nexovue/tools/rebalanceprobe

go 1.27.0

// packages/contracts is a workspace member, never published. The replace keeps
// `go mod tidy` and any non-workspace build resolving it from the repo.
replace github.com/nexovue-tech/nexovue/packages/contracts => ../../packages/contracts

require (
	github.com/google/uuid v1.6.0
	github.com/nexovue-tech/nexovue/packages/contracts v0.0.0-00010101000000-000000000000
	github.com/twmb/franz-go v1.21.6
	go.opentelemetry.io/proto/otlp v1.11.0
	golang.org/x/sync v0.22.0
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/grpc-ecosystem/grpc-gateway/v2 v2.29.0 // indirect
	github.com/klauspost/compress v1.18.7 // indirect
	github.com/pierrec/lz4/v4 v4.1.26 // indirect
	github.com/twmb/franz-go/pkg/kmsg v1.13.1 // indirect
	golang.org/x/net v0.57.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.40.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20260720211330-0afa2a65878a // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260720211330-0afa2a65878a // indirect
	google.golang.org/grpc v1.82.1 // indirect
)
