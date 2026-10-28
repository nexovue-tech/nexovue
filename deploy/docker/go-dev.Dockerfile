# Development image for every Go service. The source tree is bind-mounted, so
# this image carries only the toolchain and the reloader — one image, rebuilt
# almost never, instead of a dev stage in each service's Dockerfile.
# Production images are the two in deploy/images/.
FROM golang:1.27.0-trixie

RUN go install github.com/air-verse/air@v1.67.4

# The bind-mounted tree belongs to the host's user, which on Linux is not this container's root,
# and git then refuses it as "dubious ownership" — so `go build` cannot stamp the commit and stops
# (exit 128). Docker Desktop hides the difference on a Mac; CI's Linux runner does not.
RUN git config --system --add safe.directory /workspace

# Module and build caches survive container restarts, so a reload is not a rebuild.
ENV GOCACHE=/cache/go-build \
    GOMODCACHE=/cache/go-mod \
    CGO_ENABLED=0

# Compiler parallelism, not the running binary, sets peak memory in a capped
# container. Unbounded, `go build` is OOM-killed on the ClickHouse client
# before the service ever starts.
ENV GOFLAGS=-p=2

WORKDIR /workspace
