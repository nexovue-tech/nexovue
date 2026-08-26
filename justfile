# nexovue — one set of verbs, the same for every service.
# `just check` is the quality gate. Run it before every commit; it is what CI would run.

compose := "deploy/compose"

# go.work members. Go tooling runs per-module: `./...` from the root sees no packages.
go_modules := "apps/receiver apps/workers"

_default:
    @just --list --unsorted

# ---------- running ----------

# Dev stack: infrastructure + services with hot reload
dev:
    docker compose -f {{compose}}/docker-compose.yaml up -d --wait

# Dev stack plus doors 2 and 4b (Celery probes, Playwright browsers)
dev-doors:
    docker compose -f {{compose}}/docker-compose.yaml --profile doors up -d --wait

# Dev stack plus the demo app and load generator
dev-tools:
    docker compose -f {{compose}}/docker-compose.yaml --profile tools up -d --wait

# Production replica: same infrastructure, services from built images
dev-app:
    docker compose -f {{compose}}/docker-compose.app.yaml up -d --wait --build

# Stop everything, keep the volumes
down:
    docker compose -f {{compose}}/docker-compose.yaml --profile doors --profile tools down

# Stop everything and delete the data. Destructive.
down-hard:
    docker compose -f {{compose}}/docker-compose.yaml --profile doors --profile tools down -v

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

test: test-go test-py test-web

test-go:
    #!/usr/bin/env bash
    set -euo pipefail
    for m in {{go_modules}}; do (cd "$m" && go test ./...); done

test-py:
    #!/usr/bin/env bash
    set -uo pipefail
    uv run pytest
    code=$?
    if [ $code -eq 5 ]; then echo "no tests collected yet — see the testing policy"; exit 0; fi
    exit $code

test-web:
    pnpm -r test

# ---------- contracts ----------

# Regenerate Go and Python bindings from the envelope proto
contracts:
    cd packages/contracts && buf generate

# Lint the proto and fail on any change that would break consumers
contracts-check:
    cd packages/contracts && buf lint
    cd packages/contracts && buf breaking --against '.git#branch=main,subdir=packages/contracts'

# ---------- data ----------

# Apply all migrations: Postgres then ClickHouse
migrate: migrate-pg migrate-ch

migrate-pg:
    uv run --package nexovue-api alembic -c apps/api/alembic.ini upgrade head

migrate-ch:
    migrate -path deploy/clickhouse/migrations -database "$CLICKHOUSE_MIGRATE_DSN" up

# Roll both back one step, to prove the migrations are reversible
migrate-down:
    uv run --package nexovue-api alembic -c apps/api/alembic.ini downgrade -1
    migrate -path deploy/clickhouse/migrations -database "$CLICKHOUSE_MIGRATE_DSN" down 1

# Create one org, one owner, a production project and a known dev API key
seed:
    uv run --package nexovue-api python -m nexovue_api.seed

# ---------- performance ----------

# Measure against the budgets in docs/delivery/phase-1-plan.md section 4
load:
    go run ./tools/loadgen

# ---------- setup ----------

# Install every toolchain dependency
setup:
    go work sync
    uv sync --all-packages
    pnpm install
