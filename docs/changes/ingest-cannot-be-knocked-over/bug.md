# Bug: ingest can be knocked over — by one refused row, or by keyless gRPC exports

Group 1 of the Phase 1 Track 3 fix order (`docs/operations/production-readiness.md`): blockers 12
and 13, with the recommended items that share the work.

## Reported behavior

Found by the production-readiness review of 2026-10-10.

- **Blocker 12.** One row ClickHouse refuses in the span, metric or profile path stops the worker,
  and with it every signal for every customer. Only the log and web-vitals writers treat a refusal as
  permanent.
- **Blocker 13.** The receiver's public gRPC port reads, decompresses and holds a whole export before
  it checks the ingest key, with no limit on how many exports are held at once. Anyone who can reach
  the port can exhaust the receiver's memory without a key.

## Reproduction

### Blocker 13 — reproduced against the development receiver, 2026-10-10

A throwaway client (scratchpad, not the repository) sent `ExportTraceServiceRequest`s with **no ingest
key**, gzip-compressed, each one span whose single attribute is 15 MiB of one repeated byte — tiny on
the wire, 15,728,704 bytes decoded. Receiver memory sampled with `docker stats` every 0.3 s:

```
decoded bytes: 15728704
round 0: map[Unauthenticated:8] in 167.706333ms
round 1: map[Unauthenticated:8] in 33.86375ms
round 2: map[Unauthenticated:8] in 53.107208ms
baseline/peak samples:
261.6MiB
628.6MiB
```

Every export was refused — after being read. Eight concurrent keyless exports raised the receiver
from 262 MiB to 629 MiB of its 768 MiB limit (≈ 46 MiB each, three times the decoded size); about
sixteen would have had it OOM-killed, which was deliberately not done. Five seconds later it still held
620 MiB (`restarts=0 oom=false`): Go returns freed memory to the OS slowly, so a second burst starts
nearer the limit.

### Blocker 12 — the refusal confirmed; the crash-loop is in the plan

ClickHouse 26.3.20 on the development stack, in a temporary in-memory table (nothing written):

```
-- 100 levels of nesting
1
-- 1,100 levels of nesting
… (INCORRECT_DATA) (version 26.3.20.7 (official build))
```

A realistic sender reaches 1,100 levels with **one attribute whose key is `a.a.a.…` repeated 1,100
times**: `otlpvalue.AttributesMap` splits keys on `.` (`otlpvalue/otlpvalue.go:78`, `insertAtPath`
`:125`), so the key alone becomes the nesting. `INCORRECT_DATA` (117) is the first code
`storefailure.RefusedTheData` treats as permanent (`storefailure/refusal.go:24-35`).

The crash-loop itself is not reproduced live yet: a poisoned record stays uncommitted on Kafka, so the
development worker would crash-loop until a fixed build parks it. That run is step 2's first task
in `plan.md`, where the fixed build is the recovery.

**Ruled out as live triggers** (read, then checked against the code): invalid UTF-8 (replaced by
`json.Marshal`; protobuf decode rejects it in strings), oversized integers (OTLP integers are int64),
NaN/Infinity (`json.Marshal` fails and falls back to `{}`), ids of the wrong length (`HexID` pads or
truncates). The 2026-09 outage's own trigger — a sender setting both `context` and `context.total` —
is closed for every signal by `AttributesMap`'s collision handling (`a6762db`).

## Root cause

**Blocker 12.** The retry loop treats every insert error as weather unless the caller marks it
permanent (`consume/retry.go:71-79`); an unmarked refusal is retried for two minutes, then the process
exits and the restarted worker reads the same uncommitted batch. Only `logwriting/writer.go:43` and
`vitals/writer.go:96` mark refusals, and only the log pipeline knows what to do with one — park the
chunk's records and advance the watermark (`logwriting/pipeline.go:311-351`). The span, trace-summary,
metric, profile, service-edge, counter and endpoint-rollup writers all return a plain error. The fix
for the September outage (`consume/permanent.go`) was applied to logs only.

What the right handling is differs by writer, because of what each one holds when it inserts:

| Insert | Inside the `Absorb` that read its records? | Data | Right handling |
|---|---|---|---|
| metrics (5 tables) | Yes | Primary | Park the chunk's records, as logs do |
| profiles | Yes | Primary | Park the chunk's records, as logs do |
| spans | **No** — rows leave the sampling buffer batches later; the records are gone from memory (`consume/commit.go:9-14`) | Primary | Narrow the refusal to its rows by splitting the block, write the rest, park the refused rows themselves |
| trace summaries | No | Derived — an index of stored spans (`000028`) | Count and step over |
| service edges, counters, endpoint rollup | No — per-minute sums over many records | Derived before sampling, not re-derivable | Count and step over |

**Blocker 13.** grpc-go receives and decompresses the whole message (`recvAndDecompress`, grpc-go
v1.82.1 `server.go:1399`) before it calls the method handler (`:1443`), and our handler authenticates
inside `runExport` after decoding (`otlp/grpc.go:98-104`). `MaxRecvMsgSize` (`grpcserver.go:62`)
bounds one export; nothing bounds how many. The HTTP door does not have this shape: it authenticates
before reading the body (`otlp/handler.go:252-344`). The only hook grpc-go offers before the body is
read is the transport's tap handle (`grpc.InTapHandle`, experimental; it runs on the connection's I/O
goroutine and must not block).

## Customer impact

Nexovue is pre-deployment, so no customer is affected today.

After the first customer exists: **blocker 12** turns one unusual attribute from one customer's
service into a total ingest outage for every customer, lasting until someone finds and removes the
record by hand — it has already happened once, for an hour. **Blocker 13** lets anyone on the internet
take the receiver down with a few dozen tiny requests and no credential, dropping every customer's
telemetry while it restarts, repeatably.

## Specification impact

None. Both restore behavior the architecture already promises: ADR-002's at-least-once ingest and the
gate order in `docs/architecture/` (authenticate before anything costly).

## Regression test

Planned per task in `plan.md`; each is required to be seen failing before its fix.

## Fix plan and validation

See [`plan.md`](plan.md).

## Status

See [`status.md`](status.md).
