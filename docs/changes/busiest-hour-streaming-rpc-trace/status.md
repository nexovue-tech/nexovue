# Status: busiest-hour-streaming-rpc-trace

## Lifecycle status

**Investigated and fully root-caused. Not fixed — awaiting the founder's choice of remedy.**

## Current task

None. Waiting on the founder's decision among the three options below.

## Completed

- Reproduced the failure deterministically, twice, identical output.
- Ruled out every change made in this session as the cause (different code path, and the
  pathological trace data predates all of them).
- Ruled out a correctness bug in the trace-listing query or the trace-summary data: a wider-page
  diagnostic over the identical hour found plenty of traces the product reports correctly as
  wholly inside the window.
- Identified the exact mechanism: `ORDER BY start_time DESC` with a 25-row first page concentrates
  recently-started, still-running traces into the page the test reads, and today's session has
  accumulated an unusually high density of those — a `payment`→`flagd` gRPC event-stream
  reconnecting roughly every ten minutes since 10:46, and several long k6 browser sessions — because
  the demo has been running continuously for about seven hours, far longer than ordinary use.

## Next action

Founder: choose one —

1. **Leave it.** Self-resolving the next time the stack is rebuilt fresh, or once this hour's data
   ages past the busiest-error-hour selection on its own. No code change. Risk: reproduces again the
   next time a demo session runs this long.
2. **Harden the test.** When the first page has nothing wholly inside, look at the next page instead
   of failing — the data proves good candidates exist, just not on page one. A real, bounded
   robustness fix, and the only one of the three that stops this from recurring on a future long
   session. Recommended.
3. **Wipe ClickHouse now** (`just fresh` or a targeted delete of this tenant's data) for an immediate
   clean slate. Fast, but destructive to today's accumulated demo history — losing the F29 fixture
   data and otel-demo's other state for no gain beyond immediacy, and does not prevent a recurrence.

## Open decisions

Which of the three above. Not decided here because changing an existing, already-tuned test's
selection strategy (option 2) is a real behavior change to code with its own documented history
(`T3-7`), and wiping data (option 3) is irreversible for today's session — both are the founder's
call, not a default I should pick.

## Blocked

On the decision above.

## Risks

- Whichever option is *not* chosen stays a live risk. Option 1 leaves the test fragile to the next
  long-running session; option 3 leaves the underlying test fragility completely unaddressed, just
  hidden until data accumulates again.
- Not investigated here, and worth a separate look if it recurs: whether the ~10-minute
  `payment`→`flagd` reconnect pattern is itself worth filing upstream against the OpenTelemetry Demo,
  since it's accumulating ever-growing trace data for the life of the demo process with no natural
  end — a demo left running for days would presumably make this worse, not just today's seven hours.

## Validation

N/A — no fix implemented pending the decision above.

## Documentation impact

- `docs/changes/README.md` — this record added to the index.

## Last updated

2026-10-09
