# Production image for the worker. Multi-stage: the toolchain never ships.
FROM golang:1.27.0-trixie AS build
WORKDIR /src

# Workspace members are copied before the sources so a dependency change is the
# only thing that invalidates the module cache.
COPY go.work go.work.sum ./
COPY apps/receiver/go.mod apps/receiver/go.sum apps/receiver/
COPY apps/workers/go.mod apps/workers/go.sum apps/workers/
COPY packages/contracts/go.mod packages/contracts/go.sum packages/contracts/
RUN --mount=type=cache,target=/go/pkg/mod go mod download all

COPY packages/contracts packages/contracts
COPY apps/workers apps/workers

ARG GIT_SHA=unknown
# CGO off so the binary runs on a distroless base with no libc to match.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath \
      -ldflags="-s -w -X main.gitSHA=${GIT_SHA}" \
      -o /out/worker ./apps/workers/cmd/worker

# The worker reaches Kafka and ClickHouse; it faces nothing.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/worker /worker
EXPOSE 8081
USER nonroot:nonroot
ENTRYPOINT ["/worker"]
