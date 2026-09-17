# Feature: Synthetic checks (F18)

## Status

**Approved** — amended and re-approved by the founder 2026-09-17. Supersedes the version approved 2026-09-16, which
included browser checks: the founder has since decided to remove browser checks entirely (D13). The one
verified finding of the original current-state baseline is kept under *Dependencies and constraints*.

## Problem

A customer can watch their own APIs and sites with nexovue only by writing JSON to an API they have to
read about first. The Synthetics screen's own empty state tells them to `POST` to `/api/v1/checks`. There
is no way to create, edit, pause or delete a check in the console.

What a check can do once it exists is thin:

- **An API check is not possible.** The HTTP probe (`probes/http_check.py:100`) issues a `GET` with no
  headers and no body, so an endpoint needing a token cannot be reached, and it judges only `status < 400`
  — it never reads the body.
- **Nothing can be imported.** A developer with a cURL command, an OpenAPI description or a Postman
  collection has to retype every request by hand.
- **One location.** `prober_region` is `"local"`. A failure cannot be confirmed from anywhere else, so a
  network blip near the prober reads as the customer's outage.
- **`checks.type` is free text.** An unknown type is scheduled, raises, retries twice and vanishes with
  nothing on any screen.

**Browser checks exist and are being removed** (D13). They run customer-controlled flows in Chromium, which
for customer-written scripts would require a per-run sandbox on our infrastructure. Six of the seven
defects found on 2026-09-16 were browser-only — an empty step list counted as up, parse failures reported
as partial passes, unvalidated steps, a `fill` password returned by `GET /checks`, a browser queue growing
forever with no worker, and log records that cannot be joined. **Removing browser checks removes those six
by deletion.** The seventh, the free-text type, is fixed here.

### What a customer compares us against

Checked against each vendor's own documentation, 2026-09-16 and 2026-09-17. **✓** means that documentation
was read and states it; **✗** that it states the opposite. **?** means it was not checked — not that the
vendor lacks it.

| Expectation | Checkly | Datadog | Grafana Cloud | Better Stack |
|---|---|---|---|---|
| API checks with assertions on the response | ✓ status, headers, JSON path, regex | ✓ status, headers, body | ? | ✓ text in the response |
| Chained API requests | ✓ multistep | ✓ multistep | ✓ MultiHTTP | ? |
| **Import a cURL command** | ✓ | ? | ? | ? |
| **Import OpenAPI / Swagger** | ✓ | not found in documentation | ? | ? |
| **Import a Postman collection** | ? | not found in documentation | ? | ? |
| **Files in the request body** | ? | ✓ multipart, up to 3 files of 3 MB | ? | ? |
| Many locations | ✓ 20+ | ✓ managed locations | ✓ probes | ? |
| Retry from a different location before recording a failure | ✓ | ? | ? | ? |
| *Degraded* distinct from *failing* by response time | ✓ | ? | ? | ? |
| Encrypted variables and secrets usable in checks | ✓ | ? | ? | ? |
| Maintenance windows | ✓ | ? | ? | ? |
| SSL certificate expiry | ? | ✓ | ✓ | ✓ |
| TCP and DNS monitors | ✓ | ✓ | ✓ | ✓ |
| Heartbeat (cron) monitors | ✓ | ? | ? | ✓ |
| Browser checks | ✓ Playwright | ✓ recorded | ✓ k6 browser | ✓ Playwright |

The last row is a **deliberate gap** (D13), not an oversight.

## Goal

A developer can create, import, run, edit and understand any API, heartbeat, TCP or DNS check entirely in
the console — including authenticated, multistep and file-uploading requests — and when one fails, the
failure is confirmed, and the request and response that failed are shown.

## Target users

- **P1 Dev** — owns the API; imports its cURL commands, OpenAPI description or Postman collection; debugs the
  failure.
- **P2 Indie** — wants "tell me when my site or API is down" without instrumenting anything. vision.md names
  synthetics as *zero-instrumentation value*.

**Not served:** P3 Owner. A non-technical owner reads check health through a status page (F7, F30), not
through this feature.

## Scope

1. **API checks** — one HTTP request with **any method**, headers, query and body, and assertions on the
   response.
2. **Request bodies of every common kind** — raw text, JSON, XML, form-encoded, **multipart with files**, and
   a binary file.
3. **Multistep API checks** — requests in sequence, passing values extracted from one response to the next.
4. **Import**: a **cURL command**, an **OpenAPI description** (Swagger 2.0, OpenAPI 3.0, 3.1 and 3.2), and a
   **Postman collection** (v2.1).
5. **Heartbeat monitors** — a customer's job calls a unique URL on success; a missed call is a failure.
6. **TCP and DNS monitors.**
7. **SSL certificate expiry**, as today, available on API checks.
8. **Variables and secrets** usable in any check.
9. **Check state**: up, degraded and down, confirmed by retry before a state changes.
10. **Run now** — run a check once on demand, including before it is saved.
11. **Results**: per-run detail, history, uptime, response time — every kind reporting the same result
    metrics.
12. **Probe locations** — any number by design; two in India for hosted.
13. **Management in the console**: create, edit, pause, resume, delete, and maintenance windows.
14. **Removing browser checks completely**, and fixing the free-text check type.

## Out of scope

- **Browser checks, in any form** — scripts, step lists, recorders, screenshots. Removed (D13).
- **Running any customer-supplied code** — including Postman pre-request and test scripts, which are
  JavaScript. Imports take data, never code (BR5).
- **Notifications.** This feature decides *that* a check has changed state; telling a person is F21.
- **Status pages** (F7, F30).
- **Private locations** inside a customer's own network. Offered by Checkly and Datadog, and a separate agent
  to build, ship and support.
- **ICMP (ping) monitors.** Needs raw-socket privilege in a container and is often blocked, so a failed ping
  frequently means nothing (D2).
- **Other import formats** — HAR, Insomnia, Bruno, Postman collection v2.0 and v1.
- **OpenAPI references to other files or URLs.** An import must be self-contained; resolving a remote `$ref`
  would make the import itself fetch arbitrary URLs from our infrastructure (D14).
- **Assertions using XPath** on XML bodies. XML can be asserted as text.
- **Intervals under 60 seconds** (D5).
- **Probe locations outside India**, until a paying customer needs one (D3).
- **Monitoring as code** — a CLI or Terraform provider. The REST API serves automation meanwhile.

## User requirements

**Create and author**

- **US18.1** — As a Dev (P1), I can create an API check in the console with any method, a URL, query
  parameters, headers and a body, so that I can monitor any endpoint, including authenticated ones.
- **US18.2** — As a Dev, I can assert what a healthy response looks like — status, a header, text in the body,
  a value at a JSON path, response time — so that a `200` carrying an error is not reported as up.
- **US18.3** — As a Dev, I can chain requests so that a value from one response — a token, an id — is used in
  the next, so that I can monitor a real journey such as log in → create order → fetch order.
- **US18.4** — As a Dev, I can send files in a request, so that I can monitor an upload endpoint.
- **US18.5** — As a Dev, I can keep secrets such as API tokens as secrets that checks use but nobody can read
  back.
- **US18.6** — As a Dev, I can run a check once before saving it and see the full request and response, so that
  I find a wrong assertion now rather than one interval from now.
- **US18.7** — As an Indie (P2), I can create a basic "is this URL up" check by typing only a URL.

**Import**

- **US18.8** — As a Dev, I can paste a cURL command — from my docs or copied from my browser's developer
  tools — and get a check with its method, URL, headers and body filled in.
- **US18.9** — As a Dev, I can import my API's OpenAPI description, pick which operations to monitor, and get
  one check per operation, so that I cover an API without retyping it.
- **US18.10** — As a Dev, I can import a Postman collection I already maintain, and see exactly what was
  imported and what was skipped and why.
- **US18.11** — As a Dev, credentials found in anything I import become secrets, never plain text in a check.

**Other kinds**

- **US18.12** — As a Dev, I can monitor a cron job with a heartbeat, so that I learn when a job silently stops
  running.
- **US18.13** — As a Dev, I can check that a port accepts connections and that a domain resolves as expected.

**Understand**

- **US18.14** — As a Dev, I can see each check's state — up, degraded or down — and since when, on one screen
  with all my checks.
- **US18.15** — As a Dev, when a check fails I can see which request failed, what was sent, what came back,
  and which assertion failed with the expected and actual values.
- **US18.16** — As a Dev, I can see uptime and response time over a chosen window.
- **US18.17** — As a Dev, I can tell a check that failed from one I configured wrongly, so that my own mistake
  is never reported as my API being down.
- **US18.18** — As an Indie, when a check shows no results I am told why — not run yet, paused, in
  maintenance, or overdue.

**Trust**

- **US18.19** — As a Dev, a single failure from one location does not change my check's state; it is retried
  from another location first.
- **US18.20** — As a Dev, I can set a response time above which a check is *degraded* and a higher one above
  which it *fails*.

**Manage**

- **US18.21** — As a Dev, I can edit a check and know which run first uses the change.
- **US18.22** — As a Dev, I can pause and resume a check without losing its configuration or history.
- **US18.23** — As a Dev, I can schedule a maintenance window so that my planned deploy does not look like an
  outage.
- **US18.24** — As a Dev, before deleting a check I am told what is lost, in real numbers.

## Business rules

- **BR1** — A check must be able to fail. Every check has at least one assertion or a built-in expectation;
  an import that finds none adds a status assertion rather than creating a check that cannot fail.
- **BR2** — A probe never reaches a private, loopback, link-local or metadata address — through a redirect, a
  DNS answer that changes after validation, a customer-chosen DNS resolver, or a TCP host. Development is the
  single exception, only when two settings both say so (decision 0098).
- **BR3** — A target failing is a measurement, not an error.
- **BR4** — A stored secret is never returned by any read — API, console, logs, results, or exported data —
  and never appears in a captured request, response or error message.
- **BR5** — **nexovue never executes customer-supplied code.** Imports read data and ignore code; a Postman
  script is reported as skipped, never run and never translated.
- **BR6** — A check's state changes only on a confirmed result.
- **BR7** — A misconfigured check is reported as *misconfigured*, never as down, and never counts against
  uptime.
- **BR8** — Tenant isolation is unchanged: every read of results goes through the tenancy chokepoint.
- **BR9** — **Files a check sends are the customer's data**: stored per organization, readable only by it, and
  removed with the check and with a tenant purge.
- **BR10** — **Credentials found during import are stored as secrets**, never left in a check's configuration.

## Functional requirements

**Requests**

- **FR1** — Any valid HTTP method, including methods beyond the common seven.
- **FR2** — URL, query parameters and headers.
- **FR3** — Bodies: none, raw text, JSON, XML, form-encoded, multipart (fields and files), or a single binary
  file.
- **FR4** — Files in a request are uploaded in the console and stored with the check: **at most 3 files per
  request, 3 MB each** — Datadog's documented limit, as the market reference. **A file never attached to a
  saved check is deleted after 24 hours**, so an abandoned form leaves nothing in storage.
- **FR5** — Authentication helpers: basic, bearer token, and an API key in a header or query parameter —
  each value taken from a secret.
- **FR6** — Redirects followed by default, with an option not to; every hop judged by BR2.
- **FR7** — A time limit per request, bounded.
- **FR8** — Phase timings — DNS, TCP, TLS, time to first byte, transfer — recorded for every request, as today.

**Assertions**

- **FR9** — Each assertion has a source and a comparison. Sources: status code, a named header, body text, a
  value at a JSON path (RFC 9535), response time, TLS certificate days remaining. Comparisons: equals, does
  not equal, contains, does not contain, less than, greater than, matches a regular expression, is empty, is
  not empty.

**Multistep**

- **FR10** — Up to a bounded number of requests in order, each able to extract a value — by JSON path, header
  or regular expression — into a variable later requests use. The run stops at the first failing request and
  names it.

**Import**

- **FR11** — **Every import is preview, then create.** Nothing is saved until the user confirms. The preview
  lists what will be created, and what was skipped with the reason for each.
- **FR12** — **cURL:** method, URL, headers, body and form fields are read from a pasted command. Basic
  credentials and bearer tokens become secrets. A file reference (`-F name=@path`) cannot be read from the
  user's machine and is shown as a file the user must attach. Unsupported options are listed, not silently
  dropped.
- **FR13** — **OpenAPI:** Swagger 2.0 and OpenAPI 3.0, 3.1 and 3.2, as JSON or YAML. The user chooses a
  server and the operations to monitor. Path and query parameters take the description's examples where
  present and ask the user where not. Security schemes prompt for a secret. Each chosen operation becomes one
  check, asserting its documented success status, or `2xx` when none is documented.
- **FR14** — **Postman collection v2.1:** each request becomes a check; a folder can become one multistep
  check or separate checks, as the user chooses. Collection and folder variables become variables; an
  optional environment file supplies values. Authentication becomes secrets. **Pre-request and test scripts
  are listed as skipped** (BR5).
- **FR15** — Import files are size-limited, and a file that does not parse is refused naming where it failed.

**Other monitor kinds**

- **FR16** — **Heartbeat:** a unique, unguessable URL, an expected period and a grace time. A call within
  period plus grace keeps it up; a missed call makes it down. Accepts `GET` and `POST`, authenticated by the
  URL itself. The URL is shown once, like an API key, and can be rotated.
- **FR17** — **TCP:** a connection to a host and port succeeds within a time limit.
- **FR18** — **DNS:** a name resolves, optionally against a chosen resolver, for a record type, optionally
  asserting the answer.

**Variables and secrets**

- **FR19** — Scoped to a project, and written into a check as **`{{name}}`** for a variable — also the syntax for a
  value extracted in an earlier multistep request — and **`{{secret.NAME}}`** for a secret. `{{name}}` is Postman's own
  syntax, so an imported collection works unchanged. An unknown name makes the check *misconfigured*; it is never
  sent as literal text.
- **FR20** — A secret is write-only: created, replaced and deleted, never read.
- **FR21** — Encrypted at rest, decrypted only where a run uses it, never carried in plaintext through the
  scheduler's queue.
- **FR22** — A value matching a secret is redacted from captured requests, responses, logs and error text
  before anything is stored.

**Scheduling and locations**

- **FR23** — An interval per check from 60 seconds to 24 hours.
- **FR24** — A check runs from every selected location, each independently. Every run records its location.
  The hosted service offers two locations in India; a self-hosted install has one.
- **FR25** — A failure is retried before it is recorded — from a different location when more than one is
  selected, from the same one otherwise — a bounded number of times.
- **FR26** — A run not started before the check's next run is due is dropped, not queued.

**State**

- **FR27** — States: **up**, **degraded** (assertions passed, response time over the degraded threshold),
  **down**, **misconfigured**, **paused**, **in maintenance**.
- **FR28** — A state change is recorded with its time and the confirmed run that caused it — the event F21
  consumes.
- **FR29** — Uptime excludes misconfigured, paused and maintenance runs.

**Run now**

- **FR30** — A check can be run once on demand, saved or unsaved, showing its full result to the user who
  asked, without affecting state or history.

**Results**

- **FR31** — Every run records: time, location, attempt, outcome, duration, and per request the request sent,
  the response status, headers and body — size-limited and redacted — and any failing assertion with expected
  and actual values.
- **FR32** — History over a chosen window: uptime, response time percentiles and state changes, per location
  and combined.
- **FR33** — Every check kind reports the same result metrics — state and duration at least — so all kinds are
  read one way.
- **FR34** — Results kept for the organization's plan retention.

**Management**

- **FR35** — Create, edit, pause, resume and delete in the console, for every kind.
- **FR36** — Maintenance windows: one-off or recurring, for chosen checks or a whole project.
- **FR37** — Deleting a check states the recorded runs and stored files that will be lost before it can be
  confirmed.
- **FR38** — Validation before saving: incomplete assertions, unknown secrets, unreachable-by-rule targets and
  oversized files are refused, naming the field.

**Removal**

- **FR39** — Browser checks are removed entirely: the probe, its queue and worker, its container image and
  Playwright dependency, its contract fixture, its tests, the `browser` kind in the console's filter, and its
  documentation. Existing browser checks are deleted — nexovue is pre-deployment.

## Non-functional requirements

- **NFR1** — A new or edited check first runs within one interval plus the scheduler's refresh time, and the
  console says when.
- **NFR2** — Run now returns within the check's time limit plus a stated overhead.
- **NFR3** — Result reads use bounded, windowed queries, never scans of every run.
- **NFR4** — Files a check sends are kept in object storage, under a tenant-first key, readable only by the
  owning organization.
- **NFR5** — An OpenAPI description or Postman collection of up to 500 operations previews within a stated
  time.

## Error cases

- **EC1 — The target is down.** Retried (FR25); if confirmed, down, with the failing request and assertion
  shown.
- **EC2 — The check is misconfigured** — an unknown secret, a JSON path that does not parse, a missing file.
  State *misconfigured* with the reason; uptime unaffected (BR7).
- **EC3 — A check asserts nothing.** Refused at save, or given a status assertion on import (BR1).
- **EC4 — The target resolves to a private address.** The run fails with a refusal that says so — not a
  timeout.
- **EC5 — A cURL command uses options nexovue does not support**, or references a local file. Listed in the
  preview; nothing is silently dropped.
- **EC6 — An OpenAPI file is invalid, an unsupported version, or refers to another file or URL.** Refused,
  naming the problem (D14).
- **EC7 — A Postman collection is v1 or v2.0**, or contains scripts. Old versions refused with how to re-export
  as v2.1; scripts listed as skipped.
- **EC8 — An import contains credentials.** They become secrets; the preview says which (BR10).
- **EC9 — A file is too large, or there are too many.** Refused at upload, naming the limit.
- **EC10 — The prober itself cannot reach the internet.** With two locations, a failure is confirmed only from
  the second. **With one location this cannot be told apart from every target failing** — a stated
  limitation of single-location installs.
- **EC11 — A check has never produced a result.** The screen says which of: not yet run, paused, in
  maintenance, overdue.
- **EC12 — The customer asks to see a stored secret.** Refused; only replacement is offered.
- **EC13 — A response is very large.** The captured body is truncated for display and storage; the size
  limit is stated in the evidence.

## Acceptance criteria

**Requests and assertions**

- **AC1** — An API check must send the configured method — including a non-standard one — headers, query and
  body, verified by a test target that records what it received.
- **AC2** — A multipart request must send its fields and attached files byte-for-byte, verified by that
  target.
- **AC3** — A `200` whose JSON body fails a JSON-path assertion must be recorded as down, showing the path,
  expected value and actual value.
- **AC4** — In a multistep check, a value extracted from request 1 must reach request 2, and a failure in
  request 2 must be reported against request 2.
- **AC5** — A redirect to a private address must be refused at that hop.
- **AC6** — A fourth file, or a file over 3 MB, must be refused at upload.

**Import**

- **AC7** — Importing a cURL command with a method, headers, a JSON body and a bearer token must produce a
  preview with those values, **the token as a secret**, and nothing saved until confirmed.
- **AC8** — A cURL command with `-F file=@path` must show a file the user must attach.
- **AC9** — Importing an OpenAPI 3.2 and a Swagger 2.0 file must each list their operations and create one
  check per chosen operation, asserting the documented success status.
- **AC10** — An OpenAPI file with a `$ref` to another file or URL must be refused, and **no outbound request
  must be made** while importing it.
- **AC11** — Importing a Postman v2.1 collection with a pre-request script must create its requests, list the
  script as skipped, and **the script must not execute** — verified by a script that would make an observable
  request if run.
- **AC12** — A Postman folder imported as multistep must produce one multistep check with its requests in
  order.
- **AC13** — A Postman v2.0 collection must be refused with re-export instructions.

**Other kinds**

- **AC14** — A heartbeat not called within period plus grace must go down, and come back up on the next call.
- **AC15** — A TCP monitor against a closed port must go down; against an open one, up.
- **AC16** — A DNS monitor whose answer differs from the asserted record must go down; a resolver at a private
  address must be refused.

**Secrets**

- **AC17** — No API response, console page, stored run, log line or error message may contain a secret's value
  — verified by storing a unique marker as a secret and searching every output for it.
- **AC18** — A secret's value must not appear in plaintext in the scheduler's queue.

**State and trust**

- **AC19** — A single failure followed by a successful retry must not change the check's state.
- **AC20** — With one location, a failure must be retried from that location before the state changes.
- **AC21** — Slower than the degraded threshold with every assertion passing must record *degraded*.
- **AC22** — A misconfigured run must not reduce uptime.
- **AC23** — During a maintenance window a failing check must not change state; its runs remain visible,
  marked.

**Authoring and management**

- **AC24** — Run now on an unsaved check must return its full result and leave no history and no state.
- **AC25** — Saving a check with no assertions, an unknown secret or an oversized file must be refused before
  it is stored, naming the field.
- **AC26** — Create, edit, pause, resume and delete must be possible in the console for every kind, each covered
  by an end-to-end test.
- **AC27** — Deleting must show the number of recorded runs and stored files that will be lost.
- **AC28** — Every state from `docs/design/principles.md` rule 3 must be handled on each screen.

**Results and removal**

- **AC29** — A check of every kind must appear on the Synthetics screen with its state, uptime and response
  time; the `Kind` filter must return every kind it names.
- **AC30** — A run delivered twice must be counted once.
- **AC31** — After removal, no browser probe, queue, worker, image, Playwright dependency, fixture or `browser`
  kind may remain — verified by searching the repository.

## Dependencies and constraints

- **F21 — Alerts** consumes the state changes FR28 records. F18 ships first, so until F21 exists a state
  change is visible in the console only.
- **ADR-009 — Regions are cells.** A probe location decides where a check runs *from*, not where its results
  are stored.
- **ADR-006 / F37.** Removing browser checks removes a whole worker, image and queue from both delivery paths
  and from the self-hosted install.
- **Decision 0007** — probes run on Celery. **Decision 0098** — a probe reaches a private address only when two
  settings both say development.
- **The tenancy chokepoint.** Every ClickHouse statement is a module constant with a literal
  `tenant_id = {tenant_id:String}` conjunct (`data/clickhouse/base.py`), enforced by
  `test_tenant_chokepoint_structure.py`.
- **Rate-limit tier.** `test_ratelimit_coverage.py` puts any router reading telemetry on `TELEMETRY_TIER`;
  result reads belong with the synthetics router.
- **Secrets transport** — [ADR-010](../../decisions/ADR-010-check-secrets.md).
- **Duplicate delivery is real and must be tolerated** (retained from the previous baseline). Delivery is
  at-least-once (ADR-002). Two whole probes were redelivered in three days of development data, around a
  ClickHouse restart. Every statement in `synthetics/queries.py` collapses to
  `GROUP BY check_id, metric_name, series_hash, timestamp` before counting; `test_synthetics_live.py` proves
  the reader survives a probe stored twice. Any new result read must hold AC30 the same way.
- **What exists and is reused:** the HTTP probe's phase timings and certificate read, the address guard every
  probe connects through (`probes/addresses.py`), leader-elected scheduling, the checks CRUD API, object
  storage with tenant-prefixed keys (as source maps use), and the Synthetics screen.

## Decisions taken

By the founder, 2026-09-16 and 2026-09-17.

- **D2 — Heartbeat, TCP and DNS are in F18; ICMP is not.** Reaffirmed 2026-09-17 when browser checks were
  removed: none of the three runs customer code.
- **D3 — Any number of locations by design; two in India for hosted; one for self-hosted.** Mumbai and Hyderabad
  satisfy FR25's cross-location retry without leaving India (ADR-009).
- **D5 — A 60-second floor.** Faster intervals become a paid option later.
- **D6 — Every kind emits the same result metrics, with logs for detail** (FR33). One screen, one definition of
  uptime, and the proven `series_hash` duplicate handling reused.
- **D13 — Browser checks are removed entirely** (2026-09-17). Customer-written scripts would run strangers' code
  on our servers, requiring a fresh sandbox per run — gVisor or Firecracker — and a component that launches
  sandboxes without host-level permission. **This supersedes D1, D4, D7, D8 and D9** of the 2026-09-16 version,
  and withdraws the proposed browser-sandbox ADR, which was never accepted. It is a deliberate gap: every competitor checked offers browser checks.
- **D14 — Imports: cURL, OpenAPI (Swagger 2.0; OpenAPI 3.0, 3.1, 3.2), and Postman collection v2.1, each preview
  then create.** Checkly's documentation confirms cURL and OpenAPI import. OpenAPI 3.2.1 is current, released
  2026-09-10. **Imports must be self-contained** — a remote `$ref` is refused, because resolving it would make
  our servers fetch arbitrary URLs during import.
- **D15 — Checks can send files**, capped at Datadog's documented 3 files of 3 MB each.
- **D16 — Imported scripts are skipped, never run.** Postman scripts are JavaScript executed in Postman's
  Node.js-based sandbox; running them would bring back exactly the problem D13 removes (BR5).

## Open decisions

None.
