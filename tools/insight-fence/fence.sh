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
# **Not yet run** (status, *Validation*): written 2026-11-11 without a machine that could build
# both images. Its first run is by hand; fix it there. AC46's older snapshot (images built at
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
settings="$work/settings.env"
cat > "$settings" <<SETTINGS
NEXOVUE_PROJECT='$project'
NEXOVUE_LAYOUT='self-hosted'
NEXOVUE_APP_HOST='fence.nexovue.invalid'
NEXOVUE_INGEST_HOST='ingest.fence.nexovue.invalid'
NEXOVUE_TLS_EMAIL='fence@nexovue.invalid'
FIRST_ORGANIZATION_NAME='Fence'
FIRST_OWNER_EMAIL='owner@fence.nexovue.invalid'
FIRST_OWNER_PASSWORD='fence-owner-password'
NEXOVUE_KAFKA_BROKERS='1'
NEXOVUE_MONITORING='no'
NEXOVUE_CONNECTIONS='no'
NEXOVUE_PULL_POLICY='never'
SETTINGS
# The images exist only here; write_secrets fills in every secret.
NEXOVUE_SKIP_PORT_CHECK=1 NEXOVUE_PULL_POLICY=never "$kit/nexovue" install --settings "$settings"

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
snapshot=$("$NEXOVUE_HOME/current/nexovue" backups | grep -E '^20.*Z$' | tail -n 1)
"$NEXOVUE_HOME/current/nexovue" stop
docker volume rm "${project}_postgres-data"
"$NEXOVUE_HOME/current/nexovue" restore "$snapshot" --yes

step "AC46: setup granted again, read before anything else touches the account"
granted=$(docker exec "$project-postgres-1" psql -U nexovue -d nexovue -tAc "
    SELECT has_function_privilege('nexovue_insight', 'insight_claim_jobs(text,integer,integer)',
                                  'EXECUTE')
       AND NOT EXISTS (SELECT 1 FROM pg_proc AS p, aclexplode(p.proacl) AS a
                       WHERE p.proname LIKE 'insight\_%' AND a.grantee = 0)")
[ "$granted" = t ] || { echo "after the restore the AI account's grants are not as setup leaves them"; exit 1; }

step "the AI account, after the restore"
NEXOVUE_POSTGRES_DSN="$(postgres_dsn)" NEXOVUE_INSIGHT_POSTGRES_PASSWORD="$(
    grep '^POSTGRES_INSIGHT_PASSWORD=' "$NEXOVUE_HOME/nexovue.env" | cut -d= -f2- | tr -d "'"
)" uv run pytest -p no:cacheprovider -rs \
    "$root/apps/scheduler/tests/test_insight_account_live.py" \
    "$root/apps/api/tests/test_insight_grants_live.py"

step "the fence held"
