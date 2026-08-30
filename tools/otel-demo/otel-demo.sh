#!/usr/bin/env bash
# P1-62 — the OpenTelemetry Demo realism gate.
#
# Runs the upstream OpenTelemetry Demo — 20 containers, 18 of them sending
# telemetry, in ten languages — against our receiver, so the screens are judged
# on telemetry we did not write. Our own demo app is three Go services that
# agree with themselves; this one does not.
#
# Deliberately a separate command. It is not in `just check` — that gate must
# never need Docker — and not in `just e2e`, which runs against a stack this
# disturbs for minutes at a time.
#
# Measured on 2026-08-30, images already pulled: 1641 MiB resident across the
# 20 containers, about four minutes from `up` to the first full service map.
#
#   ./tools/otel-demo/otel-demo.sh up      bring the trimmed demo up, in waves
#   ./tools/otel-demo/otel-demo.sh stats   memory, this stack and the demo's
#   ./tools/otel-demo/otel-demo.sh logs    follow every demo container
#   ./tools/otel-demo/otel-demo.sh down    stop and remove the demo only
#
# NEXOVUE_INGEST_KEY selects the project the demo lands in. It defaults to the
# key `just seed` creates, which is also the one our own demo app uses — set it
# to a key of its own if the service map should show the demo and nothing else.
# NEXOVUE_OTEL_DEMO_SCOPE=core drops the Kafka layer on a tighter machine.

set -euo pipefail

readonly UPSTREAM_REPO="https://github.com/open-telemetry/opentelemetry-demo.git"
# Pinned. `latest` would silently change what the gate measured between runs.
readonly UPSTREAM_REF="3.0.0"

readonly HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly UPSTREAM_DIR="${HERE}/upstream"
readonly PROJECT_NAME="otel-demo"

: "${NEXOVUE_INGEST_KEY:=nvk_live_devlocaldevlocaldevloc}"
: "${NEXOVUE_OTLP_HTTP_URL:=http://localhost:14318}"
export NEXOVUE_INGEST_KEY

# Where the patched proxy config files live. Exported because compose resolves a
# relative host path against --project-directory, which is the upstream clone.
NEXOVUE_OVERLAY_DIR="${HERE}"
export NEXOVUE_OVERLAY_DIR

# Everything upstream's core and full compose files define, minus three.
#
#   otel-collector   we are the backend; its only job here was to be one
#   flagd-ui         an Elixir admin page for the flag store, no callers
#   telemetry-docs   a static documentation site, no callers
#
# Dropping them frees ~700 MB of declared limits and removes no edge from the
# service map. Everything else stays, because the point of this gate is breadth:
# eighteen services in ten languages, and the queue hop that only Kafka provides.
#
# NEXOVUE_OTEL_DEMO_SCOPE=core drops Kafka, accounting and fraud-detection with
# it, for a run on a machine that cannot spare the extra ~800 MB.
: "${NEXOVUE_OTEL_DEMO_SCOPE:=full}"

readonly WAVE_BACKING=(flagd valkey-cart astronomy-db)
readonly WAVE_SERVICES=(product-catalog quote currency shipping email payment recommendation ad cart checkout image-provider)
readonly WAVE_EDGE=(frontend frontend-proxy)
readonly WAVE_TRAFFIC=(load-generator)
# Started between the backing stores and the services: checkout produces to the
# topic, so the broker has to exist before it does.
readonly WAVE_QUEUE=(kafka accounting fraud-detection)

compose() {
    local full_layer=()
    if [ "${NEXOVUE_OTEL_DEMO_SCOPE}" = "full" ]; then
        full_layer=(-f "${UPSTREAM_DIR}/compose.full.yaml")
    fi
    docker compose \
        --project-name "${PROJECT_NAME}" \
        --project-directory "${UPSTREAM_DIR}" \
        --env-file "${UPSTREAM_DIR}/.env" \
        --env-file "${HERE}/nexovue.env" \
        -f "${UPSTREAM_DIR}/compose.yaml" \
        "${full_layer[@]}" \
        -f "${HERE}/compose.nexovue.yaml" \
        "$@"
}

fetch_upstream() {
    if [ ! -d "${UPSTREAM_DIR}/.git" ]; then
        echo "cloning ${UPSTREAM_REPO} @ ${UPSTREAM_REF}"
        git clone --depth 1 --branch "${UPSTREAM_REF}" "${UPSTREAM_REPO}" "${UPSTREAM_DIR}"
        return
    fi
    local at
    at="$(git -C "${UPSTREAM_DIR}" describe --tags --exact-match 2>/dev/null || echo unknown)"
    if [ "${at}" != "${UPSTREAM_REF}" ]; then
        echo "upstream clone is at ${at}, want ${UPSTREAM_REF} — refetching"
        git -C "${UPSTREAM_DIR}" fetch --depth 1 origin "refs/tags/${UPSTREAM_REF}:refs/tags/${UPSTREAM_REF}"
        git -C "${UPSTREAM_DIR}" checkout -f "${UPSTREAM_REF}"
    fi
}

# The demo has nothing to send to if the receiver is not up, and finding that
# out from sixteen crash loops is slower than asking once.
require_receiver() {
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${NEXOVUE_OTLP_HTTP_URL}/v1/traces" \
        -H 'content-type: application/json' \
        -H "x-nexovue-key: ${NEXOVUE_INGEST_KEY}" \
        -d '{"resourceSpans":[]}' || echo 000)"
    if [ "${code}" != "200" ]; then
        echo "receiver at ${NEXOVUE_OTLP_HTTP_URL} answered ${code}, not 200 — is \`just dev\` up, and is the key right?" >&2
        exit 1
    fi
}

report_headroom() {
    local total_mib used_mib
    total_mib=$(( $(docker info --format '{{.MemTotal}}') / 1048576 ))
    used_mib=$(docker stats --no-stream --format '{{.MemUsage}}' |
        awk -F' / ' '{print $1}' |
        awk '/GiB/{s+=$1*1024; next} /MiB/{s+=$1; next} /KiB/{s+=$1/1024} END{printf "%d", s}')
    echo "docker memory: ${used_mib} MiB in use of ${total_mib} MiB, $(( total_mib - used_mib )) MiB free"
}

wait_for_settle() {
    local seconds="$1"
    echo "  settling ${seconds}s"
    sleep "${seconds}"
}

start_wave() {
    local label="$1"
    shift
    echo "--- ${label}: $*"
    # --no-deps because the three services this gate drops are still named in
    # upstream's depends_on, and compose would start them back for us.
    compose up -d --no-deps --quiet-pull "$@"
}

cmd_up() {
    fetch_upstream
    require_receiver
    report_headroom

    start_wave "backing stores" "${WAVE_BACKING[@]}"
    wait_for_settle 15
    if [ "${NEXOVUE_OTEL_DEMO_SCOPE}" = "full" ]; then
        start_wave "queue" "${WAVE_QUEUE[@]}"
        wait_for_settle 30
    fi
    start_wave "application services" "${WAVE_SERVICES[@]}"
    wait_for_settle 45
    report_headroom
    start_wave "edge" "${WAVE_EDGE[@]}"
    wait_for_settle 45
    start_wave "traffic" "${WAVE_TRAFFIC[@]}"
    wait_for_settle 30

    report_headroom
    compose ps --format 'table {{.Name}}\t{{.Status}}'
    echo
    echo "storefront: http://localhost:8080 — telemetry is landing in the project that owns ${NEXOVUE_INGEST_KEY:0:12}…"
}

cmd_down() {
    # Scoped to this compose project by name, so it cannot reach a nexovue-*
    # container even if one were somehow in the same network.
    compose down --remove-orphans
}

cmd_stats() {
    report_headroom
    docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'
}

case "${1:-up}" in
    up) cmd_up ;;
    down) cmd_down ;;
    stats) cmd_stats ;;
    logs) shift; compose logs -f "$@" ;;
    ps) compose ps ;;
    *) echo "usage: $0 {up|down|stats|logs|ps}" >&2; exit 2 ;;
esac
