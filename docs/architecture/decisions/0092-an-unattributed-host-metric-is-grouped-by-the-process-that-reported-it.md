# 0092 — A host metric whose resource named no machine is grouped by the process that reported it, and is never called a host

- **Status:** Accepted
- **Date:** 2026-09-01
- **Applies [0082](0082-ip-named-peers-collapse-in-the-map-not-in-storage.md)'s principle to a second screen, and [0080](0080-an-unanswered-call-is-named-by-what-the-caller-asserted.md)'s prohibition on naming a node after an address**

## Context

The Infrastructure screen showed **"unnamed node"** for every row, which read as broken. It was
not broken, and it was also not merely a labelling problem — that framing is what this ADR exists
to correct.

`NodeKey` was `(host, container)` in `apps/api/src/nexovue_api/infrastructure/service.py`, so
**every resource carrying neither name collapsed into a single node**. Measured against the dev
store at 2026-09-01T10:38Z, across the thirteen metric names the screen reads:

```
host_metric_rows  without_host_name  services  instances
           18738              18738         1          2
```

Every host-metric row in the store carries no `host.name`, and they come from **two distinct
`service.instance.id` values**. So the one node was not one machine's readings under a poor label:
it was two processes' readings added together. Same grain, before and after the change, on real
rows:

```
nodes_before  nodes_after
           1            2
```

The row counts move upward while the OpenTelemetry Demo runs — an earlier pass on the same store
counted 5,723 rows and a second 6,844 — but the two instances and the collapse are stable.

The readings arrive from the demo's `recommendation` service, Python
`SystemMetricsInstrumentor`, whose resource carries `service.name`, `service.instance.id`,
`service.namespace`, `service.version` and `telemetry.sdk.*` — and no host or container name. Its
environment is the whole explanation:

```
OTEL_SERVICE_NAME=recommendation
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=3.0.0,service.criticality=medium
```

No `host.name`, and no `OTEL_EXPERIMENTAL_RESOURCE_DETECTORS`.

**Why this is the worst class of defect this screen can have.** A missing label is visible and a
reader distrusts it. A merged row is *invisible* — it renders as a plausible machine with plausible
numbers, and its CPU came from one process while its memory came from another. Today the demo runs
one instrumented service, so the merge is degenerate; the moment a customer runs two, the screen
starts reporting arithmetic nobody can defend.

## Decision

**A resource that named neither a host nor a container is identified by the process that reported
it: `service_name` (already a promoted column on `metrics_gauge` and `metrics_sum`) together with
`resource_attrs.service.instance.id`.**

Three things follow, and each is deliberate.

### `reporter` is a third `NodeKind`, not a host with a fallback name

`NodeKind` was `Literal["host", "container"]` and is now
`Literal["host", "container", "reporter"]`. A reporter is never presented as a host, and the label
is **`reported by recommendation`** — never the bare service name, which would read as a machine.

`service.instance.id` identifies a *process*. Naming a machine after one is exactly the mistake
[0080](0080-an-unanswered-call-is-named-by-what-the-caller-asserted.md) rejected when it refused to
let a bare IP name a service-map node, and it is the same trade
[0082](0082-ip-named-peers-collapse-in-the-map-not-in-storage.md) answered by keeping the most
specific truth available: presentation can aggregate upward and can never recover detail it
discarded. This ADR answers it the same way, one screen over. A test asserts the negative directly
— `nodeName` must not return `"recommendation"` — because the honest label and the dishonest one
differ by three words.

### The reporter is blanked whenever the resource named itself, in SQL

`if(names_itself, '', service_name)` in the inner read of `infrastructure/queries.py`, so the SQL
`GROUP BY` grain and the node grain are one expression rather than two that must agree.

This is load-bearing, not tidiness. Selecting the reporter unconditionally and collapsing in
Python would group *more finely* than a node and then merge afterwards, and the roll-up sums the
latest value of each series it is handed — so one named host reported by two agents would render
with **twice its memory**. That is a regression this change could easily have introduced; the live
suite asserts against it with two `service_name` values writing the same host's
`system.memory.usage`.

### The reporter's readings are shown as a host's are

`system.cpu.utilization` from an unattributed resource is still a machine's CPU fraction — a
host's readings arriving without a host's name, not a different measurement. The screen's CPU cell
and utilization bar therefore gate on `container`, never on `host`. Dropping data a customer sent
is the worst failure an observability product has, and it is why we warn on cardinality rather than
cap it.

### The screen says what is missing and how to fix it

Once per fleet, above the table, naming the reporters:

> `recommendation` is reporting host metrics without setting `host.name`, so these readings cannot
> be attributed to a machine. They are grouped by the process that sent them instead, which keeps
> each row's numbers coherent and over-counts the fleet: three instrumented services on one machine
> appear as three rows. To fix it: set `host.name` on the resource —
> `OTEL_RESOURCE_ATTRIBUTES=host.name=$(hostname)` works in every SDK, the OpenTelemetry Python SDK
> detects it for itself with `OTEL_EXPERIMENTAL_RESOURCE_DETECTORS=host`, and a collector scraping
> on the machine's behalf sets it with the `resourcedetection` processor's `system` detector.

**The remedy was run, not read about.** Inside the live `recommendation` container:

```
opentelemetry-sdk 1.43.0
detectors: ['host', 'os', 'otel', 'process', 'service_instance']
env var: OTEL_EXPERIMENTAL_RESOURCE_DETECTORS
host detector attrs: {'host.name': 'fc075e6e405c', 'host.arch': 'aarch64'}
```

That is proof the fix works for this exact SDK version, which a documentation link is not. The
SDK-neutral `OTEL_RESOURCE_ATTRIBUTES` spelling leads because
`OTEL_EXPERIMENTAL_RESOURCE_DETECTORS` is experimental and Python-specific — the name says so, and
it is not in the OpenTelemetry specification. `host.name` itself is **Recommended** and still marked
*development* ([semconv host resource](https://opentelemetry.io/docs/specs/semconv/resource/host/)).
The collector branch is the contrib
[`resourcedetection` README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/resourcedetectionprocessor/README.md):
*"By default `host.name` is being set to FQDN if possible"*, `hostname_sources: ["dns", "os"]`.

The banner earns its place because it is the only part of this that ends the problem: a customer
who sets `host.name` once never sees a reporter row again, and the rows collapse into real machines
for the right reason.

## Alternatives considered

**Group on `service.name` alone, without the instance id — rejected, and it is the tempting one.**
It is stable across restarts, which is precisely what makes it wrong: two instances of one service
on two machines merge back into one row, which is the defect this ADR exists to fix. It trades a
correct answer for a tidy one.

**Keep `(host, container)` and only improve the label — rejected.** This is what the work was
originally framed as. It leaves CPU from one machine averaged with memory from another and puts a
better caption on it, which makes the wrong number *more* credible rather than less.

**Name the node after `service.instance.id`, or after the service name with no "reported by"
— rejected.** Both assert a machine we were never told about. 0080 rejected the same move for
`network.peer.address` and the argument transfers without modification.

**Blank the reporter in Python rather than in SQL — rejected, measured.** Grouping in ClickHouse by
`(host, container, service_name, instance_id)` and merging in Python double-counts a named host
reported by two agents, because the roll-up sums the latest value of every series it receives. With
the blanking removed the live suite reports `assert 2 == 1` — one host split into two rows, each
holding half its readings.

**Collapse a reporter's restarts for display, the way 0082 collapses IP-named peers — rejected.**
See Consequences; this is the one a future reader is most likely to reopen.

## Consequences

**The inventory is over-counted, in two distinct ways, and both were found rather than assumed.**

1. **Three instrumented services on one machine are three rows.** Nothing in an unattributed
   resource says they share a machine, so there is nothing to group them by.
2. **A service that restarted five times inside the window is five rows.** The Python SDK's
   `ServiceInstanceIdResourceDetector` mints a fresh UUID per process start. Our own store already
   shows this: `recommendation` reported as `4d6173c8-7e68-45bd-a79e-80d4ce39ea3a` from 08:09 to
   09:43 and as `f8261ce4-a897-4972-bf95-f5a082e92539` from 10:17, read back through the API as two
   rows carrying their own coherent CPU and memory.

The lesser evil, deliberately. Each row's numbers stay defensible; the merged row's did not.

**Restarts are left uncollapsed, and that is not an inconsistency with 0082.** The service map
collapses address-named peers because those genuinely are *one dependency wearing several names* —
the same Postgres, redialled. Two process starts of one service are **two different processes**, and
their readings do not belong summed. Worse, we have no evidence the second start is even on the same
machine as the first: an unattributed resource says nothing about where it ran, so a collapse would
be inventing a continuity we cannot observe. If a flapping service makes the list unreadable, the
honest fix is the banner — setting `host.name` collapses those rows correctly and for the right
reason. **Do not add a display-level collapse here believing it follows 0082.**

**A `NodeKind` a caller does not know about now exists on the wire.** `apps/web` is the only
consumer today and it was changed with the API, but any future client switching on `kind` must
handle a third value. The screen's own near-miss is the warning: the CPU cell gated on
`kind === "host"` and would have rendered a blank column for every reporter — data the customer sent,
silently dropped, and reported as a bug the following week. It now gates on `container`, matching
`formatCpu`, with a test for each direction.

**The signals strip's node count changed meaning.** `uniqExact((host, container))` became
`uniqExact((host, container, reporter, reporter_instance))`, so a project's node count rises the
moment unattributed reporters exist. It is the same grain as the node list, which is the point.

**Verified, with counts.** `just check` exit 0, zero `FAIL`: Go 28 packages `ok`, Python 1,417
passed, web 456 passed across 29 files, ruff/mypy/biome clean, APCA contrast suite all `ok`. The
infrastructure suites were run and read individually — 26 offline plus 7 live against real
ClickHouse, and 34 in the web feature file. Six guarantees were negative-controlled: each was broken
deliberately, the failure watched, and the code restored. Live through the API against the running
demo, the two `recommendation` instances came back as two rows with their own readings.

**Not verified: the screen was never rendered.** The browser session did not survive the recent
wipe and no screenshot was taken; the label, banner and tooltip are covered by unit tests and the
TypeScript build, and the layout is unconfirmed.
