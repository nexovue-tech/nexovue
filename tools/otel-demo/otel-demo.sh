#!/usr/bin/env bash
# P1-62, then P1-63 — the OpenTelemetry Demo, and every door it can reach.
#
# Runs the upstream OpenTelemetry Demo — 21 containers, 18 of them sending
# telemetry, in ten languages — against our receiver, so the screens are judged
# on telemetry we did not write. Our own demo app was three Go services that
# agreed with themselves; this one does not.
#
# **It opens five doors, not one.** P1-62 fed door 1 and left four screens empty,
# which is a strange thing for the daily traffic source to do. Now:
#
#   door 1  every service reports, and so does the storefront's own browser —
#           whose spans join the backend's in one trace
#   door 1  a hostmetrics collector of ours, the only container here that is not
#           upstream's, because no application service can report its own host
#   door 2  two checks doors.py creates, probing the storefront
#   door 3  our browser SDK, injected into the shop's pages by Envoy
#   door 4a a signed deploy event carrying the release the pages report
#
#   door 4b is **not** opened. `astronomy-db` has pg_stat_statements preloaded
#           and a monitoring_user granted, and the poller refuses password auth
#           for self-hosted Postgres (ADR 0076). The Database screen stays empty.
#
# Deliberately a separate command. It is not in `just check` — that gate must
# never need Docker — and not in `just e2e`, which runs against a stack this
# disturbs for minutes at a time.
#
# Measured on 2026-09-06, images already pulled: 2007 MiB resident across the
# 21 containers, about four minutes from `up` to the first full service map.
# It was 1641 across 20 before the collector and k6's browser scenario.
#
# Needs `just dev`, which now starts the probe workers too: without them the
# checks are created and never probed, and `up` refuses rather than pretending.
#
#   ./tools/otel-demo/otel-demo.sh up      bring the trimmed demo up, in waves
#   ./tools/otel-demo/otel-demo.sh stats   memory, this stack and the demo's
#   ./tools/otel-demo/otel-demo.sh logs    follow every demo container
#   ./tools/otel-demo/otel-demo.sh down    stop and remove the demo only
#
# NEXOVUE_INGEST_KEY selects the project the demo lands in. It defaults to the
# key `just seed` creates, which is also the one our own demo app uses — set it
# to a key of its own if the service map should show the demo and nothing else.
# NEXOVUE_SITE_KEY is the browser half of the same choice: the storefront's own
# page reports through the browser door, which authenticates on a public key and
# the Origin it was served from. It must be a key whose allowed origins include
# the storefront — `just seed` writes one that does.
# NEXOVUE_OTEL_DEMO_SCOPE=core drops the Kafka layer on a tighter machine.

set -euo pipefail

readonly UPSTREAM_REPO="https://github.com/open-telemetry/opentelemetry-demo.git"
# Pinned. `latest` would silently change what the gate measured between runs.
readonly UPSTREAM_REF="3.0.0"

readonly HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly UPSTREAM_DIR="${HERE}/upstream"
readonly PROJECT_NAME="otel-demo"

: "${NEXOVUE_INGEST_KEY:=nvk_live_devlocaldevlocaldevloc}"
: "${NEXOVUE_SITE_KEY:=nvp_live_devlocaldevlocaldevloc}"
# The release the storefront's pages report as service.version, and the version a
# deploy event carries. Timestamped because each `up` genuinely is a deploy of
# this demo, and a release that never changes cannot demonstrate a release.
: "${NEXOVUE_RUM_RELEASE:=${UPSTREAM_REF}-$(date -u +%Y%m%d%H%M)}"
: "${NEXOVUE_OTLP_HTTP_URL:=http://localhost:14318}"
export NEXOVUE_INGEST_KEY
export NEXOVUE_SITE_KEY
export NEXOVUE_RUM_RELEASE

# Where the patched proxy config files live. Exported because compose resolves a
# relative host path against --project-directory, which is the upstream clone.
NEXOVUE_OVERLAY_DIR="${HERE}"
export NEXOVUE_OVERLAY_DIR

# The browser SDK's built bundle, which the storefront's pages load. Exported for
# the same reason, and checked in require_browser_sdk because `dist/` is
# gitignored — a missing build would otherwise surface as compose refusing to
# mount a file, which says nothing about what to do.
NEXOVUE_BROWSER_SDK_DIST="$(cd "${HERE}/../.." && pwd)/packages/browser-sdk/dist"
export NEXOVUE_BROWSER_SDK_DIST

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

# Ours, and first. It depends on nothing demo-side, so a misconfiguration should
# surface in seconds rather than after four minutes of application startup.
readonly WAVE_HOST_METRICS=(host-metrics)
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

# The storefront's pages carry our browser SDK, which is built rather than
# checked in. Without it compose fails on a missing bind-mount source, which
# names a path and not a remedy.
require_browser_sdk() {
    if [ -f "${NEXOVUE_BROWSER_SDK_DIST}/nexovue-rum.min.js" ]; then
        return
    fi
    echo "the browser SDK is not built, so the storefront has no script to load." >&2
    echo "  build it with: pnpm --filter @nexovue/browser-sdk build" >&2
    exit 1
}

# The checks this opens are probed by the scheduler, which is a separate compose
# profile. Without it they are created and then never run, and the Synthetics
# screen reads as broken for a reason nothing on it explains.
require_probe_workers() {
    if [ -n "$(docker ps --quiet --filter name=scheduler-light --filter status=running)" ]; then
        return
    fi
    echo "the probe workers are not running, so door 2's checks would never be probed." >&2
    echo "  start them with: just dev" >&2
    exit 1
}

# Door 2 and door 4a need something created before they have anything to report.
# Failing here fails `up`, deliberately: a demo that came up and quietly opened
# four doors of five is worse than one that says which one it could not.
open_doors() {
    echo "--- doors: checks and deploy events"
    NEXOVUE_UPSTREAM_REF="${UPSTREAM_REF}" python3 "${HERE}/doors.py"
}

cmd_up() {
    fetch_upstream
    require_receiver
    require_browser_sdk
    require_probe_workers
    report_headroom

    start_wave "host metrics" "${WAVE_HOST_METRICS[@]}"

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

    # Last, because the storefront has to be answering before a check is pointed
    # at it and before a deploy of it is announced.
    open_doors

    report_headroom
    compose ps --format 'table {{.Name}}\t{{.Status}}'
    echo
    echo "storefront: http://localhost:8080 — telemetry is landing in the project that owns ${NEXOVUE_INGEST_KEY:0:12}…"
}

cmd_down() {
    # Scoped to this compose project by name, so it cannot reach a nexovue-*
    # container even if one were somehow in the same network — and the same
    # scoping is what makes `-v` safe.
    #
    # `-v` because the demo declares no named volumes but its images do: the
    # Postgres data directory and Kafka's log dirs are anonymous volumes, and a
    # `down` without `-v` orphans a fresh set of them on every run. It is also
    # the honest behaviour for a demo — the next `up` regenerates every byte of
    # it, so keeping the old astronomy_db rows and Kafka offsets buys nothing and
    # makes "is this a clean run?" unanswerable.
    compose down -v --remove-orphans
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
