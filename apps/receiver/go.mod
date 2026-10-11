module github.com/nexovue-tech/nexovue/apps/receiver

go 1.27.0

require (
	github.com/caarlos0/env/v11 v11.4.1
	github.com/google/uuid v1.6.0
	github.com/jackc/pgx/v5 v5.10.0
	github.com/lmittmann/tint v1.2.0
	github.com/nexovue-tech/nexovue/packages/contracts v0.0.0
	github.com/prometheus/client_golang v1.24.1
	github.com/twmb/franz-go v1.21.6
	go.opentelemetry.io/proto/otlp v1.11.0
	go.opentelemetry.io/proto/otlp/collector/profiles/v1development v0.4.0
	go.opentelemetry.io/proto/otlp/profiles/v1development v0.4.0
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260825221802-da73d73af1c5
	google.golang.org/grpc v1.82.1
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/grpc-ecosystem/grpc-gateway/v2 v2.29.0 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	github.com/klauspost/compress v1.19.1 // indirect
	github.com/kylelemons/godebug v1.1.0 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/pierrec/lz4/v4 v4.1.26 // indirect
	github.com/prometheus/client_model v0.6.2 // indirect
	github.com/prometheus/common v0.70.1 // indirect
	github.com/prometheus/procfs v0.21.1 // indirect
	github.com/twmb/franz-go/pkg/kmsg v1.13.1 // indirect
	golang.org/x/net v0.57.0 // indirect
	golang.org/x/sync v0.22.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.41.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20260720211330-0afa2a65878a // indirect
)

// packages/contracts is a workspace member, never published. The replace keeps
// `go mod tidy` and any non-workspace build resolving it from the repo.
replace github.com/nexovue-tech/nexovue/packages/contracts => ../../packages/contracts
