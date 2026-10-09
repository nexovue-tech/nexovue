# Status: token-count-masking-false-positive

## Lifecycle status

**Implemented and verified, live.** Uncommitted.

## Current task

None.

## Completed

- `apps/workers/internal/pipeline/mask/rules.go`: `"token"` removed from the bare-substring
  `credentialKeyMarkers` list; a dedicated `containsSingularToken` now matches it everywhere except
  immediately before a trailing `s`, so `access_token`/`session_token`/`api_token` are still caught
  and `gen_ai.usage.input_tokens`/`output_tokens` (and any other `*_tokens` count) are not.
  Comments on both files corrected: "three collector services" is unrelated to this change, not
  touched.
- Regression coverage: a new `token_falsepositive_test.go` mirroring the existing
  `card_falsepositive_test.go` pattern, plus two new cases in `mask_test.go`'s existing
  `TestDefaultRulesLeaveOrdinaryTelemetryAlone`.
- Verified red (old behavior restored temporarily, reverted after) and green, both directions, with
  the exact failing/passing output recorded in `bug.md`.
- Verified live against the real stack: worker restarted, `gateseed` re-run, the same fixture spans
  re-queried from ClickHouse — real token counts (842, 156, 310, 48), not the masking placeholder.
- Incidentally fixed while fixing: `tools/gateseed/payload.go` crossed golangci-lint's `goconst`
  threshold (3 occurrences) on two literal keys once the new gen_ai fixture function was added;
  named them `keyResource`/`keyStartUnixNs` alongside the file's existing `keyAttributes`/`keyName`,
  matching its own stated convention exactly.

## Next action

Founder: read the diff, then say **commit**.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **A plural credential key would now escape masking.** A key like `api_tokens` or
  `refresh_tokens`, if a customer ever used one to hold a *list* of several credential strings
  rather than a count, would no longer match — the fix cannot distinguish "plural because it's a
  count" from "plural because it's a list of several tokens" by the key name alone. Judged
  acceptable: every real-world credential-key convention checked (`access_token`, `session_token`,
  `api_token`, `refresh_token`, `csrf_token`, `bearer_token`) is singular, and no realistic
  single-attribute span or log field holds an array of several raw credentials under one key.
  Narrower than the old rule in this one specific, judged-unlikely direction; the old rule was
  destroying real data in the overwhelmingly more common direction.
- **Scope was deliberately narrow.** Only `"token"` was given word-boundary treatment; `"password"`,
  `"secret"`, `"api_key"`/`"apikey"`/`"api-key"` are unchanged, because no evidence was found of them
  producing the same class of false positive. If one surfaces later, it gets its own bug record and
  its own regression test the same way this one did — not a speculative rewrite of the whole family
  now.
- The value-type question this bug started as — `maskedValue` always coerces to a string regardless
  of the original attribute's type — is unchanged. That is a separate, much smaller concern (masking
  *does* intend to destroy the value; the only question was ever which keys it should apply to at
  all) and was deliberately left alone rather than folded into this change.

## Validation

- `apps/workers/internal/pipeline/mask`, full package: all green, including the pre-existing `"token
  key": {"auth.access_token", ...}` case, proving the fix narrows rather than removes the rule's
  reach.
- Live: `docker restart nexovue-worker-1` (air rebuilds on the next request, no manual build step),
  `go run ./tools/gateseed`, then queried `spans` directly — `gen_ai.usage.input_tokens`/
  `output_tokens` on all three fixture rows now read as real integers (842/156, empty/empty,
  310/48), not `[masked]`.
- `just check`, full run: **this change's own gate is clean** — `apps/workers/internal/pipeline/
  mask` and `tools/gateseed` both pass in full, Go `vet`/lint clean, Python/web/contracts unaffected
  by this diff. **The overall run is not exit 0**, for a reason unrelated to this change:
  `apps/api/tests/test_traces_live.py::test_the_summary_and_the_span_grouping_name_the_same_traces`
  fails deterministically (reproduced twice in isolation, identical output both times) — 25 traces
  shared between the summary and span-grouping listing paths in the busiest project's
  highest-error hour (2026-10-09 11:00), none lying wholly inside the inner 50-minute window the
  test narrows to.

  Ruled out as caused by this change: neither `mask` nor `gateseed` touches `apps/api`'s trace
  listing logic, and the failing hour (11:00) predates every change in this diff by hours;
  `gateseed`'s own traces land at the time it's run (today, ~17:00), never inside that hour.

  Traced one step further, not fully root-caused (out of today's scope): the 25 shared traces in
  that hour include one trace (`c28742e9…`, root service `payment`) with roughly a dozen
  `trace_summaries` rows at ~600,000 ms (≈10 minutes) each, and a `load-generator` session trace at
  ~333,000 ms (≈5.5 minutes) — both well under the 50-minute window's width but apparently
  positioned, or recorded, such that none qualifies as wholly inside it. The repeated ~600-second
  duration on the payment trace looks like its own anomaly (a retry or timeout pattern, not normal
  checkout latency) and may be worth its own investigation independent of this test.

  Not fixed here: unrelated code path, a different root-cause class, and the test's own docstring
  already documents one prior edge case in this exact area (`T3-7`, 2026-09-07) — this looks like a
  second instance of the same class of fragility, triggered by today's unusually long continuous
  demo/load-generator session, not a regression this diff introduced.

## Documentation impact

- `docs/changes/README.md` — this record added to the index.
- `docs/features/llm-observability/spec.md` — no text change; this closes a risk the spec had
  already named but not yet traced to its actual cause.

## Last updated

2026-10-09
