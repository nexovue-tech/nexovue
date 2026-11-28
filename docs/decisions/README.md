# Architecture decisions

ADRs preserve why a consequential decision was made; `docs/architecture/` preserves current behavior. Create an ADR only when the decision is material, settled, and supported by repository evidence or an explicit founder decision.

## Recorded

- [ADR-001 — Normalize every ingestion door into one Kafka envelope](ADR-001-internal-envelope-and-kafka.md)
- [ADR-002 — Acknowledge only durable boundaries](ADR-002-at-least-once-delivery.md)
- [ADR-003 — Use OpenTelemetry as the primary collection contract](ADR-003-otel-first-collection.md)
- [ADR-004 — AGPL-3.0 for the platform, permissive for the browser SDK](ADR-004-agpl-with-a-permissive-browser-sdk.md)
- [ADR-005 — Contributions come with a CLA, not a DCO](ADR-005-a-cla-rather-than-a-dco.md)
- [ADR-006 — Terraform for infrastructure, with two delivery paths — Ansible on Linux servers, Helm on Kubernetes](ADR-006-terraform-with-two-delivery-paths.md)
- [ADR-007 — Migrating between clouds loses no data, and costs the console seconds](ADR-007-migrating-between-clouds.md)
- [ADR-008 — The website takes the first payment, and cloud customers are onboarded by hand](ADR-008-the-website-takes-the-first-payment.md)
- [ADR-009 — Regions are cells, and every organization belongs to one — India by default](ADR-009-regions-are-cells.md)
- [ADR-010 — Check secrets are sealed with their own key, and only their names travel through the queue](ADR-010-check-secrets.md)
- [ADR-011 — The API dispatches run-now tasks to the scheduler's broker](ADR-011-api-dispatches-run-now.md)
- [ADR-012 — The scheduler evaluates alerts, reading ClickHouse through shared, tenant-bound statements](ADR-012-the-scheduler-evaluates-alerts.md)
- [ADR-013 — Notifications are an outbox, written in the transaction that decided them](ADR-013-notifications-are-an-outbox.md)
- [ADR-014 — Telemetry erasure runs as a nightly batch, not per deletion](ADR-014-telemetry-erasure-runs-as-a-nightly-batch.md)
- [ADR-015 — Profiles are normalised at the intake, the one exception to the untouched envelope](ADR-015-profiles-are-normalised-at-the-intake.md)
- [ADR-016 — Staff work on their own host, as their own kind of account](ADR-016-staff-work-on-their-own-host.md) — **superseded by ADR-018**
- [ADR-017 — A read token is its own credential, and the read API is the routes that accept it](ADR-017-a-read-token-is-its-own-credential.md)
- [ADR-018 — nexovue's operator is a user in the console, behind a step-up](ADR-018-the-operator-is-a-user-in-the-console.md)
- [ADR-019 — nexovue ships as two images, and each role runs as its own container](ADR-019-two-images-each-role-its-own-container.md)
- [ADR-020 — Postgres carries what Redis held, Redis goes, and object storage is optional](ADR-020-postgres-carries-what-redis-held.md)
- [ADR-021 — nexovue reports its own health for an outside monitor to read](ADR-021-nexovue-reports-its-own-health-for-an-outside-monitor.md)
- [ADR-022 — A finding is the one record of a detected problem, and the outbox addresses findings or incidents](ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md) — with the F6 design, 2026-10-31
- [ADR-023 — The scheduler writes detection readings to ClickHouse, through an account that can insert into that one table only](ADR-023-the-scheduler-writes-detection-state-to-clickhouse-through-its-own-account.md) — with the F6 design, 2026-10-31
- [ADR-024 — A case is a finding's columns, with one active case per service](ADR-024-a-case-is-a-findings-columns.md) — with the F22 design, 2026-11-02
- [ADR-025 — Version numbers are counted before sampling, in their own rollup](ADR-025-version-numbers-are-counted-before-sampling-in-their-own-rollup.md) — with the F28 design, 2026-11-02; filed 2026-11-07
- [ADR-026 — Going over the service cap is recorded in ClickHouse, and the cap keeps the busiest](ADR-026-going-over-the-service-cap-is-recorded-in-clickhouse.md) — with the browser-door change, 2026-11-07
- [ADR-027 — A reliability target's history is daily counts in Postgres, not another rollup](ADR-027-an-slos-history-is-daily-counts-in-postgres.md) — with the F19 design, 2026-11-02; filed 2026-11-07
- [ADR-028 — Reliability target judging runs on `scheduler-light`, under the shared floor](ADR-028-slo-judging-runs-on-scheduler-light-under-the-shared-floor.md) — the founder's D66, 2026-11-02; filed 2026-11-07
- [ADR-029 — A release is a stored record with periods, fed by two sources, with measured beating told](ADR-029-a-release-is-a-stored-record-with-periods.md) — with the F28 design, 2026-11-02; filed 2026-11-08
- [ADR-030 — `scheduler-connections` makes every tracker call, on narrow settings](ADR-030-scheduler-connections-makes-every-tracker-call.md) — with the F22 design, 2026-11-02; filed 2026-11-08 with F22 Step 3
- [ADR-031 — A dashboard is a checked document of fixed panel kinds](ADR-031-a-dashboard-is-a-checked-document-of-fixed-panel-kinds.md) — with the F20 design, 2026-11-02; filed 2026-11-08
- [ADR-032 — A batch read is charged by the scan](ADR-032-a-batch-read-is-charged-by-the-scan.md) — with the F20 design, 2026-11-02; filed 2026-11-08
- [ADR-033 — The deploy envelope signs its id and time, and secrets are shown once](ADR-033-the-deploy-envelope-signs-its-id-and-time-and-secrets-are-shown-once.md) — with the F28 design, 2026-11-02; filed 2026-11-08
- [ADR-034 — An incident can say what it is about](ADR-034-an-incident-can-say-what-it-is-about.md) — with the F19 design, 2026-11-02; filed 2026-11-08
- [ADR-035 — Trackers are reconciled, and a webhook only rings the doorbell](ADR-035-trackers-are-reconciled-and-a-webhook-only-rings-the-doorbell.md) — with the F22 design, 2026-11-02; filed 2026-11-09 with F22 Step 4
- [ADR-038 — A burn does not repeat news, decided at the claim and released when its reason ends](ADR-038-a-burn-does-not-repeat-news.md) — with the F19 design, 2026-11-02; filed 2026-11-09 with F19 Step 5
- [ADR-039 — An explanation is a frozen evidence record, with code choosing the cause](ADR-039-an-explanation-is-a-frozen-evidence-record-with-code-choosing-the-cause.md) — with the F3 design, 2026-11-02; filed 2026-11-09 with F3 Step 1
- [ADR-040 — AI runs in its own role, with its own account and network](ADR-040-ai-runs-in-its-own-role-with-its-own-account-and-network.md) — with the F3 design, 2026-11-02; filed 2026-11-11 with F3 Step 3
- [ADR-045 — The API reads ClickHouse through its own bounded account](ADR-045-the-api-reads-clickhouse-through-its-own-bounded-account.md) — with the F20 design, 2026-11-02; filed 2026-11-11 with F20 Step 5
- [ADR-047 — One provider interface, three wire formats, no SDK](ADR-047-one-provider-interface-three-wire-formats-no-sdk.md) — with the F3 design, 2026-11-02; filed 2026-11-11 with F3 Step 4
- [ADR-055 — Chat reads through the API, as the person, on a ticket](ADR-055-chat-reads-through-the-api-as-the-person-on-a-ticket.md) — with the F4 design, 2026-11-02; filed 2026-11-11 with F4 Step 2
- [ADR-066 — A release summary is a notification about a release period, put into words at send](ADR-066-a-release-summary-is-a-notification-about-a-release-period.md) — with the F28 design, 2026-11-02; filed 2026-11-12 with F28 Step 5. Amends ADR-022
- [ADR-067 — Chat's knowledge is chunks in Postgres, found by keyword always, each chunk tied to its source by a cascade](ADR-067-knowledge-is-chunks-in-postgres-found-by-keyword-always.md) — with the F4 design, 2026-11-02; filed 2026-11-21 with F4 Step 5. Refines decision 0004
- [ADR-073 — Code from a model reaches a person only through rules in a room with no model](ADR-073-model-code-reaches-a-person-only-through-rules-in-a-room-with-no-model.md) — with the F5 design, 2026-11-02; filed 2026-11-21 with F5 Step 2b
- [ADR-074 — A general suggestion is an example from the finding's facts only](ADR-074-a-general-suggestion-is-an-example-from-the-findings-facts-only.md) — the founder's D65 to D71, 2026-11-02; filed 2026-11-21 with F5 Step 2b
- [ADR-075 — The AI rungs have their own routes, apart from rungs one and two](ADR-075-the-ai-rungs-have-their-own-routes-apart-from-rungs-one-and-two.md) — the founder's decision of 2026-11-21; filed with F5 Step 2b
- [ADR-079 — Embedding is metered spending through F3's month, proved by a check, and never stands between chat and keyword search](ADR-079-embedding-is-metered-spending-and-never-blocks-keyword-search.md) — with F4 Step 6, 2026-11-21. Builds ADR-067's point 5
- [ADR-080 — An organization may add a second connection for embeddings only, with its own key, agreement and check](ADR-080-a-second-connection-for-embeddings-only.md) — with F4 Step 6, 2026-11-21, building F4's D61
- [ADR-082 — F5 has its own GitHub App, read-only until the founder's go](ADR-082-f5-has-its-own-github-app-read-only-until-the-founders-go.md) — with the F5 design, 2026-11-02; filed 2026-11-21 with F5 Step 3
- [ADR-083 — A service's code location is its own row, for the organization with a project's override](ADR-083-a-services-code-location-is-its-own-row-with-a-projects-override.md) — with the F5 design, 2026-11-02; filed 2026-11-21 with F5 Step 3
- [ADR-084 — Source consent is per repository and folder, and worked out at each use](ADR-084-source-consent-is-per-repository-and-folder-worked-out-at-each-use.md) — with the F5 design, 2026-11-02; filed 2026-11-21 with F5 Step 3
- [ADR-091 — The AI account deletes a sent pack only through a function, never a table grant](ADR-091-the-ai-account-deletes-a-sent-pack-only-through-a-function.md) — with F5 Step 4, 2026-11-21. Refines the F5 design's grants
- [ADR-094 — A suggestion's stored code is taken back at every end, through one list](ADR-094-stored-code-is-taken-back-at-every-end-through-one-list.md) — with F5 Step 5, 2026-11-21. Adds a deleted project and a removed member to BR9's ends
- [ADR-095 — Backups hold no pack, patch or link code, and a restore takes code back again](ADR-095-backups-hold-no-pack-patch-or-link-code-and-a-restore-takes-code-back.md) — with F5 Step 5, 2026-11-21
- [ADR-096 — A patch is rebuilt by the person's own check run, and a refusal on the same inputs alerts](ADR-096-a-patch-is-rebuilt-by-the-persons-own-check-and-a-refusal-on-the-same-inputs-alerts.md) — with F5 Step 5, 2026-11-21. Settles F5 open decision 52
- [ADR-097 — Write access is off in code until the founder's recorded go](ADR-097-write-access-is-off-in-code-until-the-founders-recorded-go.md) — with F5 Step 6 (code), 2026-11-22. Builds foundation item 33's switch
- [ADR-098 — F5's evaluation runs the product's own code at every step](ADR-098-f5s-evaluation-runs-the-products-own-code-at-every-step.md) — with F5 Step 6 (code), 2026-11-22
- [ADR-100 — A draft is written in an order a crash cannot turn into two, and only as checked](ADR-100-a-draft-is-written-in-an-order-a-crash-cannot-turn-into-two.md) — with F5 Step 7, 2026-11-22
- [ADR-101 — Write routes exist only behind the founder's go, and every Step 7 path asks first](ADR-101-write-routes-exist-only-behind-the-founders-go.md) — with F5 Step 7, 2026-11-22. Builds on ADR-097
- [ADR-102 — A request gives back its Postgres connection once its caller is known, and a full pool answers busy](ADR-102-a-request-gives-back-its-postgres-connection-once-its-caller-is-known.md) — with the change *request holds one connection*, 2026-11-22. Settles F20 open decision 40
- [ADR-103 — Code from a model reaches a person or a repository only through rules in a room with no model](ADR-103-model-code-reaches-a-person-or-repository-only-through-rules-in-a-room-with-no-model.md) — F5's design (2026-11-02), filed with F5 Step 9, 2026-11-22
- [ADR-104 — The first commit on a suggestion branch holds back the customer's CI](ADR-104-the-first-commit-on-a-suggestion-branch-holds-back-the-customers-ci.md) — F5's design (2026-11-02), filed with F5 Step 9, 2026-11-22; the GitHub trial may change it
- [ADR-105 — Source consent is per repository, bound to where it goes](ADR-105-source-consent-is-per-repository-and-bound-to-where-it-goes.md) — F5's design (2026-11-02), filed with F5 Step 9, 2026-11-22
- [ADR-106 — F5 has its own GitHub App and its own code location](ADR-106-f5-has-its-own-github-app-and-its-own-code-location.md) — F5's design (2026-11-02), filed with F5 Step 9, 2026-11-22
- [ADR-088 — A case note is chat's knowledge by copy, gone with its note, inert, and ranked above nexovue's own lines](ADR-088-a-note-is-chat-knowledge-by-copy-and-ranks-above-composed-lines.md) — with F4 Step 7, 2026-11-21. Extends ADR-067 to F22's notes

## Earlier decisions, 0001–0112

Before this series, 112 decision records lived under `docs/architecture/decisions/`. They were
deleted in `e0f11a0`; their full text is intact in git history.

**[decision-log.md](decision-log.md)** indexes all 112 — what each decided, whether it still stands,
and the ten chains where a decision was superseded or reversed. Read it before proposing anything
that sounds like a fresh architecture choice; several already have an answer, including one that was
decided, reversed, and reversed back in a day.

Three of the 112 are restored in full here — **0060 → 0061 → 0075**, the counter-idempotency chain —
because they record a measured, open defect: a reproducible span overcount across a ClickHouse
restart whose stated cause was later falsified. `docs/architecture/current-system-reference.md` §18
now carries that finding.

## Not yet written

Tenant-isolation chokepoints and the ClickHouse/Postgres control-plane split are settled in code but have no ADR. Write them when the reasoning next needs to be defended or changed.
