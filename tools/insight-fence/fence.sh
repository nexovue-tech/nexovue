#!/usr/bin/env bash
# `just insight-fence` (F3 plan T3.11): the AI role's fence, on a really installed kit.
#
# CI's dev stack and `just stack` start only docker-compose.yaml, which would prove the wrong
# file; `dev-app` installs nothing, so `nexovue restore` would refuse to run there (A78). This
# builds both images from this checkout, a kit from deploy/release/kit.sh, installs it into a
# temporary NEXOVUE_HOME under its own Compose project, and then runs:
#
#   1. the `insight_fence` suites against it (AC42: what the role can reach);
#   2. `nexovue backup`, an emptied Postgres volume, and `nexovue restore <snapshot> --yes`,
#      then the AI account suites against the restored database (AC46: setup grants again,
#      onto a server that never had the account).
#
# First run: CI run 44899530725 (docs/changes/insight-fence-settings). Everything after the install
# is still unrun. AC46's older snapshot (images built at
# the merge base with main) is not here yet. Needs Docker with 12 GiB (`nexovue` preflight).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
version="0.0.0-fence.$(git -C "$root" rev-parse --short HEAD)"
project="${NEXOVUE_FENCE_PROJECT:-nexovue-fence}"
work="$(mktemp -d)"
export NEXOVUE_HOME="$work/home"
registry="ghcr.io/nexovue-tech"

cleanup() {
    docker compose -p "$project" down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

step() { printf '\n==> %s\n' "$*"; }

step "building both images as $version"
docker build -f "$root/deploy/images/nexovue.Dockerfile" -t "$registry/nexovue:$version" "$root"
docker build -f "$root/deploy/images/nexovue-pipeline.Dockerfile" \
    -t "$registry/nexovue-pipeline:$version" "$root"

step "the kit"
"$root/deploy/release/kit.sh" "$version" "$work/kit"
tar -C "$work" -xzf "$work/kit/nexovue-$version.tar.gz"
kit="$work/nexovue-$version"

step "installing under project $project"
# The installer's own path, not --settings (which takes a file whole, for a machine replacing
# another): answers come from the environment, as for any install, and `nexovue` writes every
# secret and default itself, so a variable the release Compose file newly demands cannot be
# missing here. The images exist only here, hence the pull policy.
export NEXOVUE_PULL_POLICY=never NEXOVUE_SKIP_PORT_CHECK=1
export NEXOVUE_PROJECT="$project" NEXOVUE_LAYOUT=self-hosted
export NEXOVUE_APP_HOST='fence.nexovue.invalid' NEXOVUE_INGEST_HOST='ingest.fence.nexovue.invalid'
export NEXOVUE_TLS_EMAIL='fence@nexovue.invalid' FIRST_ORGANIZATION_NAME='Fence'
export FIRST_OWNER_EMAIL='owner@fence.nexovue.invalid' FIRST_OWNER_PASSWORD='fence-owner-password'
export NEXOVUE_KAFKA_BROKERS=1 NEXOVUE_MONITORING=no NEXOVUE_CONNECTIONS=no
"$kit/nexovue" install

postgres_dsn() {
    local password
    password=$(grep '^POSTGRES_PASSWORD=' "$NEXOVUE_HOME/nexovue.env" | cut -d= -f2- | tr -d "'")
    local address
    address=$(docker inspect --format \
        "{{(index .NetworkSettings.Networks \"${project}_default\").IPAddress}}" \
        "$project-postgres-1")
    printf 'postgresql://nexovue:%s@%s:5432/nexovue' "$password" "$address"
}

step "AC42: what the AI role can reach"
NEXOVUE_FENCE_PROJECT="$project" uv run pytest -m insight_fence -p no:cacheprovider -rs \
    "$root/apps/scheduler/tests/test_insight_network_live.py"

step "AC46: backup, an empty Postgres volume, restore"
"$NEXOVUE_HOME/current/nexovue" backup
snapshot=$(find "$NEXOVUE_HOME/backups" -mindepth 1 -maxdepth 1 -type d -name '20*Z' \
    -exec basename {} \; | sort | tail -n 1)
"$NEXOVUE_HOME/current/nexovue" stop
# `stop` leaves the containers, and Docker refuses to remove a volume a container still names.
docker compose -p "$project" down
docker volume rm "${project}_postgres-data"
"$NEXOVUE_HOME/current/nexovue" restore "$snapshot" --yes

step "AC46: setup granted again, read before anything else touches the account"
"$root/tools/insight-fence/grants-check.sh" "$project-postgres-1"

step "the AI account, after the restore"
NEXOVUE_POSTGRES_DSN="$(postgres_dsn)" NEXOVUE_INSIGHT_POSTGRES_PASSWORD="$(
    grep '^POSTGRES_INSIGHT_PASSWORD=' "$NEXOVUE_HOME/nexovue.env" | cut -d= -f2- | tr -d "'"
)" uv run pytest -p no:cacheprovider -rs \
    "$root/apps/scheduler/tests/test_insight_account_live.py" \
    "$root/apps/api/tests/test_insight_grants_live.py"

step "the fence held"
