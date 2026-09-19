# Feature: Continuous profiling (F17)

## Status

**Approved** — by the founder, 2026-09-19, as written: the five decisions below were accepted as
recommended and are recorded under *Decisions taken*. F17 moved into Phase 1 Track 1 by founder
decision the same day (`feature-catalog.md` §5).

## Problem

nexovue can say *that* a service is slow and *which request* was slow, and never *which code*.

- A trace ends at the span. A customer who sees `POST /checkout` take 900 ms knows the handler is
  slow; to find the function that spends the time they leave nexovue for a profiler of their own, if
  they have one.
- **Nothing collects profiles.** The receiver accepts three OTLP paths — `/v1/traces`, `/v1/logs`,
  `/v1/metrics` (`receiver/internal/otlp/signals.go`); the internal envelope knows three signals
  (`contracts/proto/nexovue/v1/envelope.proto`); there is no table, pipeline, route or screen. The
  OTLP library in use (`go.opentelemetry.io/proto/otlp` v1.11.0) does not contain the profiles
  definitions at all.
- The only profiling in the code is Go's own `pprof` on our services' admin listener, off by default,
  for debugging nexovue itself — unrelated to customers.

The vision names "line-level CPU/memory attribution" as one of the product's pillars
(`vision.md`, pillar 5) against Pyroscope, and marks it *Not built*.

### What a customer compares us against

Researched 2026-09-19 from vendor documentation, pricing pages and OpenTelemetry project sources.
**✓** means the vendor's documentation states it; **✗** the opposite; **?** not verified.

| Expectation | Grafana Pyroscope | Datadog | Polar Signals / Parca | Sentry | Elastic |
|---|---|---|---|---|---|
| Zero-code collection (eBPF) | ✓ CPU only | ✓ preview | ✓ | ✗ SDK only | ✓ |
| SDK / language-agent collection | ✓ | ✓ | ✗ | ✓ | ✗ |
| Takes OTLP profiles | ✓ Pyroscope 2.0 | via its OTel host profiler | ✓ | ✗ | ✓ ships the receiver |
| Flame graph + top-functions table | ✓ | ✓ | ✓ | ✓ | ✓ |
| Compare two ranges or versions (diff) | ✓ | ✓ | ✓ | ✓ | ? |
| From a span to its profile | ✓ CPU, five languages | ✓ seven languages | ✓ Go only | ✓ | ? |
| CPU, heap/alloc, locks… | ✓ varies by language | ✓ | CPU, memory | CPU/wall | CPU |
| Retention | 14–30 days | 8 days flame graphs | 30 days | ? | ? |
| Price anchor | $0.40/GB written | ~$19 per host | ~$3 per vCPU-month | $0.0315 per profile-hour | ? |

**What users complain about:** native frames that show as `[unknown]` or hex addresses because symbols
never reached the backend; overhead well above the advertised ~1 % in some setups; flame graphs people
cannot read ("width is not time"); spans under ~10 ms that never get a sample at 100 Hz; and cost that
scales with hosts or cores.

**The platform fact that shapes this feature:** OpenTelemetry's profiles signal entered **public
alpha in March 2026**. Its OTLP format is *Development* — sent to `/v1development/profiles`, package
`opentelemetry.proto.profiles.v1development` — and it broke compatibility four times in
sixteen months (proto v1.7.0 May 2025, v1.8.0 Sep 2025, v1.9.0 Oct 2025, v1.10.0 Mar 2026). The
OpenTelemetry profiling group advises against relying on it for anything critical and says no
production-ready backends exist yet. Its whole-system eBPF profiler (donated by Elastic) runs as a
Collector receiver on Linux ≥ 5.10 and covers native code, Go, Rust, JVM, Python, Ruby, PHP, Node.js,
.NET, Perl and Erlang with no code change, at ~1 % CPU.

## Goal

A customer who already sends OpenTelemetry to nexovue adds profiling to their Collector, and from then
on can open any service and see which functions spend its CPU (and memory, where their runtime
reports it), over any time range, compare before and after a deploy, and go from a slow span straight
to the code it ran — without installing a nexovue agent.

## Target users

- **P1 Dev** — owns the service; wants to know which function made the p95 regress after a deploy,
  and to reach it from the trace they were already reading.
- **P2 Indie** — one person; wants "why is my server at 90 % CPU" answered by a picture, not a
  profiling tool of their own.

**Not served:** P3 Owner — profiles are code, and there is nothing in them for a non-technical owner.
**Not served either: browser and mobile profiling** (Sentry's UI profiling). The browser SDK collects
Web Vitals and errors (F2), not stacks.

## Scope

- **Intake of OTLP profiles**, over OTLP/HTTP and OTLP/gRPC, from any OpenTelemetry source — the
  eBPF profiler receiver, the Collector's `pprof` receiver, or a language SDK that exports OTLP
  profiles — authenticated, rate-limited and allowance-checked exactly as the other signals are.
- **Storage** per tenant and project, with retention, masking, usage metering and tenant erasure like
  the other signals.
- **Every profile type a source sends** — CPU first, because that is what the eBPF profiler produces;
  heap, allocations, locks, wall time and the rest when an SDK or `pprof` sends them — each shown under
  its own name and unit.
- **A Profiles screen**: a service's aggregated flame graph and top-functions table for a chosen time
  range and profile type, filtered by host, version and environment, with search inside the graph.
- **Compare**: the same service across two time ranges, or two versions, as a difference.
- **From a span to its profile**: where samples carry the span they ran under, the trace view offers
  that span's profile.
- **On the service page**: whether the service is being profiled, and the way into its profile.
- **Setup documentation**: the Collector configuration for the eBPF profiler and for `pprof`, and what
  each language gets.
- **An honest preview label** while OpenTelemetry's profiles format is not stable.

## Out of scope

- **A nexovue profiling agent.** The product's stated difference is "OTel-only, no agent"
  (`feature-catalog.md`); profiles arrive through OpenTelemetry's own profiler and Collector.
- **Symbol upload** (debug files, `debuginfod`). Native frames the source could not name are shown as
  unsymbolized, not named — see *Decisions taken*, D3.
- **Profiles from browsers and mobile apps.**
- **Alerts on profiles** (e.g. "this function's CPU share rose"). F21's conditions are unchanged.
- **Automatic regression findings** ("this function got slower after deploy") — the insight layer is
  Phase 2 (`feature-catalog.md` §5); F17's catalog line about "finding evidence" waits for it.
- **An AI explanation of a flame graph.**
- **The legacy `pprof`-over-HTTP push API** of Pyroscope and its SDKs. A customer on those converts
  through the Collector's `pprof` receiver.

## User requirements

- **US17.1** — As a Dev (P1), I can add profiling by changing my OpenTelemetry Collector's
  configuration, following a nexovue page, without code changes and without a nexovue agent.
- **US17.2** — As a Dev, I can open a service and see where its CPU went over the last hour as a
  flame graph, and as a list of the functions that spent the most.
- **US17.3** — As a Dev, I can narrow that to one host, one version or one environment, and to any
  time range, and search the graph for a function by name.
- **US17.4** — As a Dev, I can compare the service before and after a deploy, and see which functions
  now spend more and which less.
- **US17.5** — As a Dev, from a slow span in a trace, I can see the code that span ran, when the
  profile recorded it.
- **US17.6** — As a Dev, I can switch between the profile types my runtime sends — CPU, memory,
  allocations, locks — and read each in its own unit.
- **US17.7** — As an Indie (P2), I can read a flame graph without already knowing how: the screen tells
  me what width means and where to look first.
- **US17.8** — As a Dev, I can tell when a service is not being profiled at all, or its profiles
  stopped arriving, as opposed to profiling showing nothing interesting.
- **US17.9** — As a Dev, I can tell when a frame is unnamed because symbols were missing, rather than
  mistaking it for my code.

## Business rules

- **BR1 — Tenancy.** A profile belongs to one organization and one project, as every signal does; no
  read, filter or comparison may cross them.
- **BR2 — Collected by OpenTelemetry only.** nexovue does not ship or require a profiling agent.
- **BR3 — Masking applies.** Profile attributes (labels on samples, resources and the profile) are
  masked by the project's masking rules before storage, exactly as span and log attributes are.
  Function names, file names and line numbers are code, not personal data, and are not masked.
- **BR4 — The unstable format is contained.** OpenTelemetry's profiles format is expected to change
  before it is stable. A change to it must be absorbable by changing nexovue's intake alone, without
  migrating stored profiles, changing the screens, or losing data already stored.
- **BR5 — Retention, erasure and metering as for the other signals.** Profiles expire with the
  project's retention, are erased with the tenant (ADR-014), and are metered per organization.
- **BR6 — No invented readings.** A range with no profiles shows that there are none, never an empty
  flame graph that reads as "nothing ran"; a frame the source could not name is marked as such.

## Functional requirements

### Intake

- **FR1** — The receiver accepts OTLP profiles on the OTLP-defined path for the version it supports
  (`/v1development/profiles` today) over HTTP (protobuf and JSON) and gRPC, on the existing OTLP
  ports, behind the same gates as the other signals: authentication, rate limit, organization and
  allowance checks.
- **FR2** — A profiles request in a format version nexovue does not support is refused with an OTLP
  error that names the supported version, and counted as refused; it is never accepted and then
  dropped.
- **FR3** — Accepted profiles follow the at-least-once contract (ADR-002): the response is written
  only after the broker has the data.
- **FR4** — Usage is metered in **samples** — the unit a flame graph is built from — per organization
  and day, beside spans, log records and data points. (OTLP's `partial_success` counts rejected
  *profiles*, not samples; a request is accepted or refused whole, so the door never reports one.)

### Storage

- **FR5** — Each sample is stored with its stack (as functions, files and lines, leaf first), its
  value(s) and unit per profile type, its time, its service, host, version and environment, its
  attributes after masking, and its trace and span ids when the source linked it (OTLP `link`).
  A value is kept in its own profile type's unit: the eBPF profiler sends *CPU samples*, counted.
  A process that sends no `service.name` — a whole-host profiler sees every process — is stored as
  `unknown_service:<executable>`, as OpenTelemetry's SDKs name an unnamed service.
- **FR6** — A frame without a function name is stored with what identifies it (its binary and
  address) and flagged unsymbolized.
- **FR7** — Profiles are retained per the project's retention for profiles (D2) and erased with the
  tenant.

### Reading

- **FR8** — **Profiles screen**: pick a service, a profile type the service has sent, and a time
  range (default last hour); see an aggregated flame graph (icicle, root at top) and a table of
  functions by self and total value; filter by host, version and environment; search highlights
  matching frames. Above them, the profile type's value over time, from which a range can be selected.
- **FR9** — **Compare**: choose a baseline and a comparison — two time ranges, or two versions — for
  one service and profile type; the flame graph shows each function's change in its share of the
  total, marked as more, less, or present on one side only; the table ranks by change. Shares, not
  absolute values, are compared, so ranges of different length or load compare fairly.
- **FR10** — **Span to profile**: a span in the trace view offers its profile when samples linked to
  that span exist; the flame graph then aggregates only those samples. When the service is profiled but
  no sample landed in the span, the span says so and why (spans shorter than a sampling interval rarely
  catch one).
- **FR11** — **Service page**: shows whether the service sent profiles in the selected range, when it
  last did, which types, and links to its Profiles screen.
- **FR12** — **Reading aid**: the flame graph explains itself where it is shown — width is the share of
  the total, not time; the widest frame on top is where to look first — and a frame's tooltip gives its
  function, file and line, self and total value in the type's unit, and its share.
- **FR13** — **Preview label**: every profiling screen and the setup page say that profiling rests on
  a preview OpenTelemetry signal, until the signal is declared stable and nexovue supports that version.

### Setup

- **FR14** — A documentation page gives the Collector configuration for the eBPF profiler (Linux ≥
  5.10, privileges it needs) and for the `pprof` receiver, pointed at the customer's nexovue endpoint
  and key; says which languages get which profile types from each; and says which get span links.

## Non-functional requirements

- **NFR1 — No effect on the other signals.** Profiles have their own door and pipeline; a flood or a
  failure of profiles must not delay or refuse spans, logs or metrics.
- **NFR2 — Reading speed.** A service's flame graph for one hour renders in under 3 s at the p95 for a
  service reporting from up to 20 hosts; a day in under 10 s.
- **NFR3 — Storage.** Profiling at the eBPF default sampling rate costs no more than 100 MB of stored
  profile data (compressed) per busy CPU core per day, measured before release; the storage design
  records how the figure was met. *Restated per busy core by the founder, 2026-09-19 (D6): the eBPF
  profiler samples busy cores, so a per-host figure depends on how busy the host is. Measured: ~73 MB.*
- **NFR4 — Graph size.** A flame graph shows at most a bounded number of frames, and says when it
  merged the smallest ones, so a huge profile cannot freeze the browser.

## Error cases

| What happens | What the user sees | Can they recover | Distinguishable from a normal empty result |
|---|---|---|---|
| The service sends no profiles | "Not profiled" with the setup link | Yes: set up the Collector | Yes |
| Profiles stopped arriving | Last received time on the service page and the Profiles screen | Yes | Yes |
| The range has no profiles but others do | "No profiles in this range" and the nearest range with data | Yes | Yes |
| The Collector sends an unsupported format version | The Collector logs our refusal naming the supported version; nothing is stored | Yes: use the documented Collector version | Yes (refusal counted) |
| Native frames without symbols | Frames shown as `binary+offset`, marked unsymbolized, with a note on why | Not in F17 (D3) | Yes |
| A span has no linked samples | The span says profiling saw none, and why | No | Yes |
| The allowance is spent | Profiles refused like other signals, with the same notice | Yes: plan | Yes |
| Masking removed an attribute a filter used | The filter shows the masked value like other signals | By changing masking | Yes |

## Acceptance criteria

- **AC1** — A Collector configured as the setup page says, with the eBPF profiler on a Linux host
  running a Go and a Python service, must result in CPU flame graphs for both services on the Profiles
  screen within 2 minutes.
- **AC2** — A `pprof` heap profile sent through the Collector's `pprof` receiver must appear as its own
  profile type with its own unit, beside CPU.
- **AC3** — A profiles request without a valid key must be refused like any other signal, and one for a
  suspended or deleted organization likewise.
- **AC4** — A profiles request in an unsupported format version must be refused with an error naming
  the supported version, and must store nothing.
- **AC5** — The broker must hold a batch before its request is answered; a broker outage must produce
  a retryable error, not an accepted-and-lost batch.
- **AC6** — A sample attribute matching a project masking rule must be stored masked; function and file
  names must be stored unmasked.
- **AC7** — One organization's profiles must never appear in another's screen, filter or comparison
  (tenant-isolation suite extended).
- **AC8** — Filtering by host, version and environment must change the flame graph and table to that
  subset, and searching a function name must highlight every frame of it.
- **AC9** — A comparison of two versions where one function's CPU share doubled must show that function
  as increased and rank it first by change.
- **AC10** — A span whose samples carry its span id must offer its profile, and that profile must
  contain only those samples; a profiled span with no linked samples must say so.
- **AC11** — Frames without names must render as unsymbolized, marked, never as blank or as another
  function.
- **AC12** — A range with no profiles must say so and must not render an empty flame graph.
- **AC13** — Samples must be metered per organization per day, and the samples accepted must equal the
  samples metered.
- **AC14** — Profiles older than the project's profile retention must not be returned, and a tenant's
  profiles must be erased by the tenant erasure (inventory test extended).
- **AC15** — A flood of profiles that exhausts their pipeline must not delay spans, logs or metrics
  from the same organization (NFR1).
- **AC16** — Every profiling screen and the setup page must carry the preview label (FR13).
- **AC17** — Every state in `docs/design/principles.md` rule 3 must be handled on the Profiles screen.
- **AC18** — The flame graph must meet NFR2 on the development stack with profiling data from 20
  simulated hosts, measured and recorded.

## Dependencies and constraints

- **ADR-003 (OpenTelemetry first):** satisfied — profiles arrive as OTLP.
- **The envelope contract** (`envelope.proto`): *"wrapped around an untouched OTLP payload … we
  define no span/log/metric schema of our own."* Applied to profiles, the unstable format would reach
  the workers too, against BR4. **The design must resolve this** — keeping the contract and absorbing
  format versions in both intake and worker, or making profiles the one signal normalised at the
  intake. It is a design decision with an ADR, not a spec one.
- **The OTLP Go library** in use has no profiles definitions; the design names their source.
- **ADR-002** (acknowledge only durable boundaries), **ADR-014** (erasure), masking, usage metering
  and the tenant chokepoint all extend to the new signal.
- **Operating constraint:** the eBPF profiler needs Linux ≥ 5.10 and elevated privileges on the
  customer's host; macOS and Windows hosts get profiles only from SDKs.
- **ClickHouse memory** (`docs/changes/gate-found-defects/`): profile tables are likely to be the
  largest by row count; the design sizes them against the development cap.

## Decisions taken

Accepted by the founder with the specification, 2026-09-19.

- **D1 — How profiles count against the plan.** Samples have no fixed size, and a profiled host sends
  far more rows than it does spans. **Decided:** meter and show samples from day one, but do not
  count them against the allowance while the feature is in preview; set the allowance rule when the
  signal is stable and real volumes are known. *Alternative:* count them now, which needs a price
  per sample that nothing yet justifies.
- **D2 — Profile retention.** **Decided:** the project's retention, capped at 14 days — the low end
  of the market (Pyroscope free 14, Datadog 8 for flame graphs, others 30), because profiles are the
  largest signal. *Alternative:* the project's full retention.
- **D3 — Symbolization.** **Decided:** out of F17; show unsymbolized native frames honestly (FR6,
  AC11) and add symbol upload as its own feature once OpenTelemetry settles how symbols travel (no
  standard exists yet). Runtimes the eBPF profiler names on the host — Go, JVM, Python, Ruby, PHP, Node,
  .NET — are unaffected. *Alternative:* build a symbol-upload endpoint and CLI now.
- **D4 — Where it lives in the console.** **Decided:** a *Profiles* item in the navigation under
  the investigation screens, entered also from the service page (FR11) and from spans (FR10).
- **D5 — The preview label.** **Decided:** yes, as FR13, removed only when OpenTelemetry declares
  the signal stable and nexovue supports that version.

- **D6 — NFR3 per busy core** (2026-09-19, after measurement). Stored bytes follow busy cores, not hosts:
  ~73 MB per busy core per day was measured against a per-host target of 100 MB. **Decided:** state the
  target per busy core (option A). Storing less per sample — dropping the profiler's CPU and thread-id tags
  and keeping sample times to the minute, ~51 MB — is kept for when a paying customer's fleet is sized.

## Open decisions

None.
