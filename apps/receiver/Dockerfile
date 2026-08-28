# Production image for the receiver. Multi-stage: the toolchain never ships.
FROM golang:1.27.0-trixie AS build
WORKDIR /src

# go.work is a local development convenience and is deliberately not copied:
# it names modules that have no reason to be in this image, and enumerating
# them here meant the image stopped building the day one was added. The replace
# directive in go.mod is what resolves packages/contracts, so the module builds
# standalone. Manifests come before the sources so a dependency change is the
# only thing that invalidates the module cache.
ENV GOWORK=off
COPY apps/receiver/go.mod apps/receiver/go.sum apps/receiver/
COPY packages/contracts/go.mod packages/contracts/go.sum packages/contracts/
RUN --mount=type=cache,target=/go/pkg/mod go -C apps/receiver mod download

COPY packages/contracts packages/contracts
COPY apps/receiver apps/receiver

ARG GIT_SHA=unknown
# CGO off so the binary runs on a distroless base with no libc to match.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go -C apps/receiver build -trimpath \
      -ldflags="-s -w -X main.gitSHA=${GIT_SHA}" \
      -o /out/receiver ./cmd/receiver

# nonroot: the ingest edge is the internet-facing process here.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/receiver /receiver
EXPOSE 4317 4318 8081
USER nonroot:nonroot
ENTRYPOINT ["/receiver"]
