# The `nexovue-pipeline` image (ADR-019): the receiver, the worker, the dead-letter tool and the
# setup step that prepares ClickHouse and Kafka. The command names the binary — `/receiver`,
# `/worker`, `/dlq`, `/setup` — since the image holds no shell.
#
# Built from the repository root: docker build -f deploy/images/nexovue-pipeline.Dockerfile .
FROM golang:1.27.3-trixie AS build
WORKDIR /src

# go.work is a development convenience and is not copied: each module's replace directive
# resolves packages/contracts, so each builds standalone. Manifests come before sources so a
# dependency change is the only thing that invalidates the module cache.
ENV GOWORK=off CGO_ENABLED=0
COPY packages/contracts/go.mod packages/contracts/go.sum packages/contracts/
COPY apps/receiver/go.mod apps/receiver/go.sum apps/receiver/
COPY apps/workers/go.mod apps/workers/go.sum apps/workers/
RUN --mount=type=cache,target=/go/pkg/mod \
    go -C apps/receiver mod download && go -C apps/workers mod download

COPY packages/contracts packages/contracts
COPY apps/receiver apps/receiver
COPY apps/workers apps/workers

ARG REVISION=unknown
ARG CREATED=unknown
# The build facts health and /metrics report (internal/buildinfo): the .git directory is not in
# the build context, so the toolchain cannot stamp them itself. Until 2026-10-28 the images set
# `main.gitSHA`, which no package declares, and every image reported "unknown".
ENV RECEIVER_BUILDINFO=github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo \
    WORKERS_BUILDINFO=github.com/nexovue-tech/nexovue/apps/workers/internal/buildinfo
# CGO off so the binaries run on a distroless base with no libc to match.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go -C apps/receiver build -trimpath \
      -ldflags="-s -w -X ${RECEIVER_BUILDINFO}.revision=${REVISION} -X ${RECEIVER_BUILDINFO}.buildTime=${CREATED}" \
      -o /out/receiver ./cmd/receiver && \
    go -C apps/workers build -trimpath \
      -ldflags="-s -w -X ${WORKERS_BUILDINFO}.revision=${REVISION} -X ${WORKERS_BUILDINFO}.buildTime=${CREATED}" \
      -o /out/worker ./cmd/worker && \
    go -C apps/workers build -trimpath -ldflags="-s -w" -o /out/dlq ./cmd/dlq && \
    go -C apps/workers build -trimpath -ldflags="-s -w" -o /out/setup ./cmd/setup

# nonroot: the receiver faces the internet, and nothing here needs more.
FROM gcr.io/distroless/static-debian12:nonroot
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="nexovue-pipeline" \
      org.opencontainers.image.description="nexovue: the receiver, the worker and the setup step" \
      org.opencontainers.image.source="https://github.com/nexovue-tech/nexovue" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
COPY --from=build /out/receiver /out/worker /out/dlq /out/setup /
# Where /setup reads the ClickHouse migrations from by default.
COPY deploy/clickhouse/migrations /migrations/clickhouse
EXPOSE 4317 4318 8081
USER nonroot:nonroot
