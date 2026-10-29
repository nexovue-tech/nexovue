#!/usr/bin/env bash
# Builds a release's install kit, and optionally its offline bundles (design.md §6; ready-to-deploy,
# T15). release.yml runs it; so can anyone, to see what a customer unpacks.
#
#   deploy/release/kit.sh <version> <out-dir>                 nexovue-<version>.tar.gz
#   deploy/release/kit.sh <version> <out-dir> amd64 arm64     … and nexovue-<version>-offline-<arch>.tar
#
# The kit is everything a host needs except the images: the `nexovue` script, the Compose files,
# the edge's and the stores' configuration, the backup scripts and units, the monitor, and the
# self-hosting guide. An offline bundle adds every image the install runs, for that architecture,
# and a SHA256SUMS over all of it that `nexovue install --offline` checks before loading a byte.
# Bundles need Linux's bash and sha256sum, as on the release runner.
set -euo pipefail

version="${1:?version}"
out="$(mkdir -p "${2:?out-dir}" && cd "$2" && pwd)"
shift 2
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
name="nexovue-$version"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
kit="$staging/$name"

mkdir -p "$kit/compose" "$kit/clickhouse" "$kit/docs"
cp "$root/deploy/nexovue" "$kit/"
# The install layouts and the stores they include. The development stack and the replica's
# build-from-source overlay stay behind.
cp "$root/deploy/compose/nexovue.yaml" "$root/deploy/compose/nexovue.hosted.yaml" \
    "$root/deploy/compose/nexovue.quickstart.yaml" "$root/deploy/compose/nexovue.kafka3.yaml" \
    "$root/deploy/compose/compose.infra.yaml" "$root/deploy/compose/compose.infra.deploy.yaml" \
    "$kit/compose/"
cp -R "$root/deploy/caddy" "$root/deploy/postgres" "$root/deploy/seaweedfs" "$root/deploy/backup" \
    "$root/deploy/systemd" "$root/deploy/monitoring" "$kit/"
# The migrations travel inside the pipeline image, not here.
cp -R "$root/deploy/clickhouse/config.d" "$root/deploy/clickhouse/users.d" "$kit/clickhouse/"
cp -R "$root/docs/self-hosting" "$kit/docs/"
cp "$root/LICENSE" "$kit/"
printf '%s\n' "$version" > "$kit/VERSION"

tar -C "$staging" -czf "$out/$name.tar.gz" "$name"
echo "kit: $out/$name.tar.gz"

# Every image the install can run: each layout and the monitoring profile, at this version.
settings="$staging/images.env"
cat > "$settings" <<SETTINGS
NEXOVUE_VERSION=$version
POSTGRES_DB=x
POSTGRES_USER=x
POSTGRES_PASSWORD=x
CLICKHOUSE_DB=x
CLICKHOUSE_USER=x
CLICKHOUSE_PASSWORD=x
CLICKHOUSE_READER_PASSWORD=x
CLICKHOUSE_ERASER_PASSWORD=x
KAFKA_CLUSTER_ID=x
WORKERS_MASKING_HASH_SECRET=x
CHECK_SECRET_KEY=x
CREDENTIAL_ENCRYPTION_KEY=x
S3_BUCKET=x
S3_ACCESS_KEY_ID=x
S3_SECRET_ACCESS_KEY=x
NEXOVUE_APP_HOST=x
NEXOVUE_INGEST_HOST=x
NEXOVUE_TLS_EMAIL=x
SETTINGS

for arch in "$@"; do
    bundle="$staging/offline-$arch/$name"
    mkdir -p "$bundle"
    tar -C "$staging" -cf - "$name" | tar -C "$staging/offline-$arch" -xf -
    mkdir -p "$bundle/images"
    mapfile -t images < <(docker compose --env-file "$settings" \
        -f "$kit/compose/nexovue.yaml" -f "$kit/compose/nexovue.kafka3.yaml" \
        --profile monitoring config --images | sort -u)
    for image in "${images[@]}"; do
        docker pull --quiet --platform "linux/$arch" "$image" >/dev/null
    done
    docker save "${images[@]}" | gzip -6 > "$bundle/images/nexovue-images-$arch.tar.gz"
    ( cd "$bundle" && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | sort | xargs sha256sum > SHA256SUMS )
    tar -C "$staging/offline-$arch" -cf "$out/$name-offline-$arch.tar" "$name"
    # Removed so the next architecture's pull is not answered by this one's.
    docker image rm "${images[@]}" >/dev/null 2>&1 || true
    echo "offline bundle: $out/$name-offline-$arch.tar ($(du -h "$out/$name-offline-$arch.tar" | cut -f1))"
done
