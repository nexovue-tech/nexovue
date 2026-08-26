# Development image for every Go service. The source tree is bind-mounted, so
# this image carries only the toolchain and the reloader — one image, rebuilt
# almost never, instead of a dev stage in each service's Dockerfile.
# Production images are per-service and live in apps/<service>/Dockerfile.
FROM golang:1.27.0-trixie

RUN go install github.com/air-verse/air@v1.67.4

# Module and build caches survive container restarts, so a reload is not a rebuild.
ENV GOCACHE=/cache/go-build \
    GOMODCACHE=/cache/go-mod \
    CGO_ENABLED=0

# Compiler parallelism, not the running binary, sets peak memory in a capped
# container. Unbounded, `go build` is OOM-killed on the ClickHouse client
# before the service ever starts.
ENV GOFLAGS=-p=2

WORKDIR /workspace
