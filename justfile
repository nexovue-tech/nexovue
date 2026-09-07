# nexovue — one set of verbs, the same for every service.
# `just check` is the quality gate. Run it before every commit; it is what CI would run.

compose := "deploy/compose"

# go.work members. Go tooling runs per-module: `./...` from the root sees no packages.
go_modules := "apps/receiver apps/workers packages/contracts tools/rebalanceprobe"

# Migration targets. Both default to the dev stack's published ports; override
# either to point at another environment.
postgres_dsn := env("POSTGRES_DSN", "postgresql+asyncpg://nexovue:nexovue_dev@localhost:" + env("POSTGRES_HOST_PORT", "15432") + "/nexovue")
# golang-migrate's ClickHouse driver reads credentials from query parameters and
# ignores URL userinfo — a DSN in the usual user:pass@host form authenticates as
# `default` and fails with a confusing password error.
clickhouse_migrate_dsn := env("CLICKHOUSE_MIGRATE_DSN", "clickhouse://localhost:" + env("CLICKHOUSE_NATIVE_HOST_PORT", "19000") + "?username=nexovue&password=nexovue_dev&database=nexovue&x-multi-statement=false")

_default:
    @just --list --unsorted

# ---------- running ----------

# The Celery probe workers were behind a `doors` profile until 2026-09-06, on the
# reasoning that the full stack is rarely needed at once — but `just otel-demo up`
# is the daily traffic source and refuses without them, so the extra step was a
# papercut on the common path. Measured idle: 393 MiB across four containers
# (scheduler-beat runs two replicas and elects a leader).
# Dev stack: infrastructure + every service, hot reloading, probe workers included
dev:
    docker compose -f {{compose}}/docker-compose.yaml up -d --wait

# Production replica: same infrastructure, services from built images
dev-app:
    docker compose -f {{compose}}/docker-compose.app.yaml up -d --wait --build

# Keeps *our* volumes — stores, broker, caches. The demo's go, because they are
# anonymous volumes its images create and its next `up` regenerates.
# Stop everything, keep the volumes. Takes the OpenTelemetry Demo with it.
down:
    -./tools/otel-demo/otel-demo.sh down
    docker compose -f {{compose}}/docker-compose.yaml down

# Destructive. Everything not in a snapshot from `just backup` is gone, including
# Kafka's buffer, and it leaves no schema and no seed — so `just fresh` is usually
# what you actually want. To keep the data, take a backup first.
# Stop everything and delete every volume: stores, broker, caches
down-hard:
    -./tools/otel-demo/otel-demo.sh down
    docker compose -f {{compose}}/docker-compose.yaml down -v

# Run this when the dev stack has drifted into a state you stopped trusting.
# Takes about two and a half minutes, most of it waiting for ClickHouse and Kafka.
#
# The build is here and not in `dev` on purpose. Compose has never rebuilt on a
# changed Dockerfile — docker/compose#1487, open since 2015 — so `--build` is the
# only mechanism, and it costs 47s even fully cached, which is too much on a
# command run many times a day. `fresh` is where it belongs: it is the command
# for a state you stopped trusting, a stale image is exactly that state, and
# `down-hard` has just deleted the Go build cache — so the cold compile that
# follows is the one that fails when an image is missing GOFLAGS=-p=2.
# Wipe everything and rebuild: down-hard, build, up, topics, migrate, seed
fresh: down-hard
    docker compose -f {{compose}}/docker-compose.yaml build
    just dev
    just topics
    just migrate
    just seed
    @echo
    @echo "Fresh stack up, migrated and seeded."
    @echo "  Traffic:  just otel-demo up     five doors; the key defaults to the seeded one"
    @echo "  App:      http://localhost:15173"
    @echo
    @echo "  Two screens stay empty until you act: RUM needs a page loaded"
    @echo "  (open http://localhost:8080, or run just load), and Database is"
    @echo "  blocked on ADR 0076."

logs service="":
    docker compose -f {{compose}}/docker-compose.yaml logs -f {{service}}

# ---------- quality ----------

# Everything CI would run. The pre-commit gate.
check: fmt-check lint test contracts-check

fmt:
    #!/usr/bin/env bash
    set -euo pipefail
    for m in {{go_modules}}; do (cd "$m" && go fmt ./...); done
    uv run ruff format .
    pnpm exec biome format --write .

fmt-check:
    #!/usr/bin/env bash
    set -euo pipefail
    unformatted=$(gofmt -l {{go_modules}})
    if [ -n "$unformatted" ]; then echo "not gofmt'd:"; echo "$unformatted"; exit 1; fi
    uv run ruff format --check .
    pnpm exec biome format .

lint: lint-go lint-py lint-web

lint-go:
    #!/usr/bin/env bash
    set -euo pipefail
    for m in {{go_modules}}; do (cd "$m" && golangci-lint run ./...); done

lint-py:
    uv run ruff check .
    uv run mypy .

lint-web:
    pnpm exec biome check .
    # biome does not typecheck, so without this a type error passes every gate.
    pnpm -r --if-present run typecheck

test: test-go test-py test-web

test-go:
    #!/usr/bin/env bash
    set -euo pipefail
    # -count=1 because a cached "ok" is not evidence a test ran, and this gate
    # has twice reported green on a suite that executed nothing (rule 12).
    # `vet -tags storeprobe` compiles the tagged probes without running them:
    # they need a live control plane, but a file nothing compiles rots unseen.
    for m in {{go_modules}}; do (cd "$m" && go vet -tags storeprobe ./... && go test -count=1 ./...); done

test-py:
    #!/usr/bin/env bash
    set -uo pipefail
    # `.env` if it exists, because the live suites need what it holds. Every
    # other one gates on a service `just dev` starts and reachable on a known
    # localhost port; the mail suite gates on a credential, which no compose
    # file can provide. Without this it raises during collection and takes the
    # offline suites with it — a gate that fails for a reason unrelated to the
    # code is a gate people learn to ignore.
    [ -f .env ] && { set -a; . ./.env; set +a; }
    uv run pytest
    code=$?
    if [ $code -eq 5 ]; then echo "no tests collected yet — see the testing policy"; exit 0; fi
    exit $code

test-web:
    pnpm -r --filter '!@nexovue/e2e' test

# ---------- end to end ----------

# Deliberately NOT part of `just check` — the pre-commit gate must not require docker.
# Playwright against the running stack (P1-60). Bring the stack up first.
e2e:
    pnpm --filter @nexovue/e2e test

# The same suite with a visible browser, for working on a failing spec
e2e-headed:
    pnpm --filter @nexovue/e2e test --headed

# The OpenTelemetry Demo — 16 services, 10 languages — against our receiver (P1-62).
# Never part of `just check` or `just e2e`: it wants ~2.5 GB and several minutes,
# and it is a realism gate run deliberately, not a dependency of anything.
# Bring the stack up first. `just otel-demo down` stops only the demo.
otel-demo command="up":
    ./tools/otel-demo/otel-demo.sh {{command}}

# Destructive by design, and never part of `just check`.
# Kill the broker and the store under load; assert nothing acknowledged was lost (P1-27).
# -count=1 is load-bearing, not a habit: without it Go serves a cached pass and
# the suite reports success having executed nothing. That has now happened three
# times here — a missing build tag, a self-skipping suite, and this.
chaos:
    cd tools/chaos && go test -tags chaos -v -count=1 -timeout 20m ./...

# ---------- contracts ----------

# Regenerate Go and Python bindings from the envelope proto
contracts:
    cd packages/contracts && buf generate

# Lint the proto and fail on any change that would break consumers
contracts-check:
    cd packages/contracts && buf lint
    cd packages/contracts && buf breaking --against '../../.git#branch=main,subdir=packages/contracts'

# ---------- data ----------

# Apply all migrations: Postgres then ClickHouse
# Auto-creation is off on the broker, so a wiped Kafka volume comes back with no
# topics and the receiver refuses every export with UNKNOWN_TOPIC_OR_PARTITION —
# silently, at ~330 refusals a minute. `fresh` calls this; run it by hand after
# any `down-hard`.
# Create the ingestion topics on the running broker
topics:
    ./deploy/kafka/topics.sh

# Apply all migrations: Postgres then ClickHouse
migrate: migrate-pg migrate-ch

migrate-pg:
    POSTGRES_DSN="{{postgres_dsn}}" uv run --package nexovue-api alembic -c apps/api/alembic.ini upgrade head

migrate-ch:
    migrate -path deploy/clickhouse/migrations -database "{{clickhouse_migrate_dsn}}" up

# Roll ONE store back one step, to prove that store's migrations are reversible.
#
# There is deliberately no combined `migrate-down`. A migration belongs to one
# store, so "one step back in each" is never a coherent operation — it just took
# the other store down a version and back, recreating whatever tables that
# migration owns and losing their rows. It caught two people and cost
# `project_first_traces` its rows (ADR 0079). Name the store you mean.

migrate-down-pg:
    POSTGRES_DSN="{{postgres_dsn}}" uv run --package nexovue-api alembic -c apps/api/alembic.ini downgrade -1

migrate-down-ch:
    migrate -path deploy/clickhouse/migrations -database "{{clickhouse_migrate_dsn}}" down 1

# Create one org, one owner, a production project and a known dev API key
seed:
    POSTGRES_DSN="{{postgres_dsn}}" uv run --package nexovue-api python -m nexovue_api.seed

# ---------- backup ----------

# Snapshots land here. Outside every Docker volume on purpose: `just down-hard`
# deletes volumes, and a backup that dies with the thing it protects is a file.
backup_dir := env("NEXOVUE_BACKUP_DIR", justfile_directory() / ".backups")

# Writes a Postgres dump, a ClickHouse archive, a manifest of every table's row
# count and both schema revisions, and checksums. Safe to run on a live stack;
# stop ingest first if you want the manifest's row counts to be exact.
# Snapshot both stores into .backups/<timestamp>/
backup:
    NEXOVUE_BACKUP_DIR="{{backup_dir}}" ./deploy/backup/backup.sh

# List the snapshots taken so far
backups:
    @ls -1 "{{backup_dir}}" 2>/dev/null || echo "no snapshots yet — run: just backup"

# Restore ONE store from a snapshot, into an empty one. There is deliberately no
# combined `restore`, for the reason `migrate-down` gives one section up: the two
# stores fail separately, are restored separately, and the operator has to know
# which one they mean.
#
# Neither takes a migration first — both snapshots carry their own schema and the
# revision that produced it. Stop the writers, restore, then check, in the order
# docs/delivery/runbooks/restore-from-backup.md gives.

# Restore the control plane from a snapshot
restore-pg snapshot:
    NEXOVUE_BACKUP_DIR="{{backup_dir}}" ./deploy/backup/restore-postgres.sh {{snapshot}}

# Restore the telemetry store from a snapshot
restore-ch snapshot:
    NEXOVUE_BACKUP_DIR="{{backup_dir}}" ./deploy/backup/restore-clickhouse.sh {{snapshot}}

# Compares row counts per table, both schema revisions, and that no ingest
# watermark sits above the broker offset it names. This is what "the restore
# worked" means; the restore commands exiting 0 is not.
# Check a running stack against a snapshot's manifest
backup-verify snapshot:
    NEXOVUE_BACKUP_DIR="{{backup_dir}}" ./deploy/backup/verify.sh {{snapshot}}

# ---------- performance ----------

# Runs on this machine, not in a container, and that is not incidental: its
# endpoint defaults to localhost:14318, which is the receiver's published port
# here and nothing at all inside a container. A `just dev-tools` that got this
# wrong shipped for months sending zero spans.
# Measure against the budgets in docs/delivery/phase-1-plan.md section 4
load:
    go run ./tools/loadgen

# ---------- setup ----------

# Install every toolchain dependency
setup:
    go work sync
    uv sync --all-packages
    pnpm install
