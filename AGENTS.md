# nexovue engineering operating manual

## Repository working rules

These rules take priority when they are more specific than the workflow in
[`docs/engineering/workflow.md`](docs/engineering/workflow.md).

1. **Never push without Raj's permission.** A commit is permitted when appropriate; pushing happens
   only when Raj explicitly says "push" for that occasion. Never add an AI co-author trailer.
2. **Use Raj's Git identity only.** Rely on the repository's existing Git configuration. Never pass
   `-c user.name` or `-c user.email`.
3. **Never use `gh` in this repository.** It is authenticated to an account without access to the
   nexovue-tech organization. Use Git over SSH where appropriate; hand GitHub-specific work to Raj.
4. **Code is current-state truth.** When code, tests, migrations, or deployment configuration
   disagree with documentation, say so and update the relevant document.
5. **Fix root causes everywhere they exist.** Do not substitute a temporary patch or workaround.
   If the root correction is large or changes scope, explain it and let Raj decide the scope.
6. **Build to Code Complete standards.** Use intention-revealing names, focused routines, low
   coupling, named constants instead of magic values, and deliberate invalid-input handling.
7. **Keep comments short.** Explain non-obvious *why* in a line or two. Rewrite code that needs a
   long explanatory comment.
8. **Stop rather than guess about material uncertainty.** Finish independent work, then state the
   missing requirement or decision and ask Raj. Never invent product behavior.
9. **Analyze a request before implementing it.** Check actual code and recorded decisions; raise
   conflicts, missing decisions, and material alternatives before writing code. If Raj reaffirms the
   direction after that, implement it fully.
10. **Research before recommending a current approach.** For libraries, component patterns,
    configuration syntax, or a question about current best practice, search authoritative web
    sources first. Report the source, selected approach, rejected alternative, and anything found
    to be outdated.
11. **Delete what a change replaces.** When code, configuration, documentation, or a database object
    is superseded, remove it in the same change unless it must remain; state what remains and why.
12. **A result is evidence only when it was observed.** Read test output and test counts; exit code
    zero alone is insufficient. Never claim a check, fact, or measurement without the evidence that
    was actually run or read. State unrun validation plainly.
13. **Explain at Raj's level.** Lead with the outcome and consequence. Define unfamiliar production
    terms on first use, retain technical precision, and do not soften real risks.
14. **Nexovue is pre-deployment.** There are no customers, production/staging systems, or live data
    to preserve. Do not retain a wrong API, schema, migration, URL, or configuration merely for
    backward compatibility. Design for the intended end state now; flag decisions that will become
    hard to reverse after the first customer.

## Source of truth

Read these before changing a meaningful part of the system:

1. `README.md` for the repository map and local commands.
2. `docs/product/vision.md` for product intent and scope.
3. `docs/product/feature-catalog.md` to identify the selected feature.
4. `docs/decisions/` for what has already been settled and why — the `ADR-NNN` series, plus
   `decision-log.md`, which indexes 112 earlier records. **Read it before proposing anything that
   sounds like a fresh choice**: several questions that sound open already have an answer, including
   one decided, reversed, and reversed back inside a day.
5. The relevant document in `docs/architecture/` for current-system truth.
6. The applicable `docs/engineering/` standards — see **Engineering standards** below for which.
7. The relevant `docs/quality/` and `docs/operations/` documents when release risk is in scope.
8. The feature or change's `status.md`, `plan.md`, `spec.md`, and `design.md`, if it exists.
9. `docs/templates/README.md` for the form each of those documents takes.

## Where a decision gets recorded

**A material, settled decision becomes an ADR in `docs/decisions/`** — not a line in a table
somewhere else. The criterion is in that directory's own README: material, settled, and supported by
repository evidence or an explicit founder decision. Add it to the README's index in the same commit,
or it is invisible to the next reader.

Do not scatter decisions across whichever document was open at the time. `vision.md`'s log carries
*product and commercial* intent — pricing, scope, who the buyer is. `docs/decisions/` carries the
consequential ones and, more importantly, **why**, including the alternatives rejected and the
evidence that decided it. That "why" is the part no amount of reading the code recovers, and it is
what stops a settled question being re-litigated by someone who never knew it was settled.

If a decision is genuinely not material enough for an ADR, it still belongs somewhere durable — the
feature's `status.md` or its catalogue entry — never only in conversation.

For current-state facts, code, migrations, tests, and deployment files are authoritative when they disagree with documentation. For intended feature behavior, an approved feature specification is authoritative; do not silently let implementation redefine it. Product vision and research are intent/evidence, never proof that a capability exists.

## Starting or resuming work

1. Inspect `git status --short`; preserve unrelated user changes.
2. Classify the request: feature, bug, refactor, migration, infrastructure, security, or operations
   change.
3. Follow [`docs/engineering/workflow.md`](docs/engineering/workflow.md) — the lifecycle, the
   proportionate workflow for each class of work, the approval gates, and the handoff form.

Report the selected classification, the documents involved, the next approval gate, and the
immediate next action before substantive work begins. A question or read-only review starts no
workflow; say so rather than opening a workspace for it.

If a requirement, acceptance criterion, or approval boundary is materially ambiguous, stop before
implementation, explain the ambiguity and its impact, and request founder direction. Do not turn an
assumption into product behavior silently.

## Engineering standards

`docs/engineering/` holds the development workflow plus twelve standards. Read the ones the
change touches before writing code,
not after review finds they were missed. Three apply to every change and are not repeated in the
table: `coding-standards.md`, `code-structure.md`, and `git.md`.

| What the change touches | Also read |
|---|---|
| `apps/api` routes, services, queries | `api.md`, `backend.md`, `testing.md`, `security.md` |
| `apps/receiver`, `apps/workers` | `backend.md`, `performance.md`, `observability.md`, `testing.md` |
| `apps/web` | `frontend.md`, `testing.md` |
| Postgres or ClickHouse schema | `database.md`, `backend.md`, `security.md`, `testing.md` |
| Compose, images, deployment | `infrastructure.md`, `observability.md`, `security.md` |
| Anything reading tenant data, or any auth path | `security.md`, plus `docs/architecture/security-and-tenancy.md` |
| Anything on a hot path or a new query | `performance.md` |

Three rules about the standards themselves:

1. **Do not invent a convention silently.** If the standards do not cover the case, say so and
   propose what the convention should be, rather than establishing one by writing code.
2. **Report a conflict rather than picking a side.** Two standards that disagree, or a standard that
   disagrees with the code, is a decision for Raj and usually means one of them is stale.
3. **Propose the update when a standard is insufficient.** A standard that no longer matches how the
   code works is worse than none, because it is trusted. Fixing it is part of the change that
   outgrew it.

## Commands and test execution

Everything goes through `just`. Use `just` alone to list recipes.

```bash
just setup                 # sync Go, Python, and TypeScript dependencies
just dev                   # local stack, topics, migrations, seed, demo traffic
just fresh                 # destructive reset plus dev stack
just check                 # offline formatting, lint, types, tests, contracts
just e2e                   # Playwright against a running stack
just chaos                 # durability test against a running stack
just migrate               # Postgres then ClickHouse migrations
just backup                # snapshot both stores
```

Go runs per module, never from repository-root `./...`; always use `-count=1`. Python live tests need the development stack and must fail loudly when unavailable. TypeScript unit tests are colocated Vitest tests; Playwright needs the stack. `just down-hard` deletes volumes and is destructive.

## Repository invariants

- The data plane is receiver → Kafka → workers → ClickHouse. The receiver acknowledges only after Kafka does; workers commit offsets only after ClickHouse does.
- Postgres is the control plane. `apps/api` owns its schema; the ingest path does not write it except workers' usage metering.
- Tenant isolation, masking before downstream processing, and pre-sampling metric derivation are load-bearing. Read the relevant architecture document before changing them.
- API and ClickHouse tenancy chokepoints, migration rules, and web import/UI rules are enforced by tests and standards. Preserve them rather than bypassing them.

## Service-specific rules

- **Receiver:** preserve `auth → origin → allowance → ratelimit → decode → normalize → produce`. Browser `text/plain` is allowed only for the browser door; trace batches partition by trace ID; refusals use OTLP status semantics.
- **Workers:** preserve `decode → mask → derive → sample → sink`. Every post-message accumulator retains partition provenance so a rebalance cannot double-count. Undecodable messages go to the DLQ before their offset advances.
- **API:** route handlers stay HTTP-only; domain packages separate schemas, services, and queries. No raw tenant ClickHouse SQL outside `data/`; tenant-scoped Postgres queries take `org_id`. Checkout never changes a plan; a signed, idempotent webhook does.
- **Scheduler:** tasks publish shared envelopes; a failed monitored target is a successful measurement. Keep browser and light queues separate.
- **Web:** imports flow `shared → features → app`; use only `shared/api-client.ts` for API calls and `app/query-client.ts` for status interpretation. Use design tokens; loading is a skeleton and missing readings render as an em dash.

## Validation and handoff

- Use `just` commands. `just check` is the offline quality gate.
- Run focused tests during development and the proportionate integration/E2E/chaos checks for changed risks. A live suite must fail loudly when its dependency is unavailable.
- Update only the authoritative documents affected by a truth change. Do not duplicate facts.
- Report completed work, validation run, residual risks, documentation/specification impact, and the next action recorded in `status.md`.

## Approval boundaries

The founder decides product scope, priorities, budget, risk acceptance, deployment target, and major architecture/business tradeoffs. The agent may recommend technical options, but must not silently choose those decisions. Never deploy, expose secrets, or perform destructive operations without explicit authorization.
