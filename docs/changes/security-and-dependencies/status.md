# Status: security-and-dependencies

## Lifecycle status

In review — built on branch `fix/send-test-race`, one pull request (founder, 2026-10-30).

## Current task

The full gate, then the pull request.

## Completed

**The flaky test.** `test_a_send_test_adds_a_job_on_the_deliveries_queue` failed CI on `main` (`8df0bb5`):
the stack's own deliveries worker serves that queue and took the job before the test read it. With a 3 s
window it failed 5 of 5 runs exactly as CI did; the test now reads inside a transaction it never commits,
which no worker can see — 0 of 5 with the same window.

**Secret scanning (2), closed as used in tests:** a fake `sk_test_` planted in the deleted demo shop for the
masking rule (ADR-004 recorded it), and the Standard Webhooks example secret used as a reference vector.

**CodeQL (13).**
- Closed as false positives with the reason: SHA-256 of API tokens, ingest keys and session fingerprints
  (long random tokens, not passwords — 3); the session token in its own `HttpOnly`, `Secure` cookie (1).
- Fixed in tests (8): set membership said as a subset (MCP hosts); URLs compared by scheme and host, not by
  text (OpenAPI import, checkout origins, the Channels test's field value); the closed port taken on the
  address the probe dials, not every interface; the CSP test reads inline scripts with `html.parser`
  instead of a pattern that misses `</script >`.
- Closed as "won't fix" by the founder's decision (2026-10-30): `apps/web/src/shared/session.ts` keeps the
  ingest key in `sessionStorage` so onboarding survives a refresh; a script injected into the console could
  already mint keys through the signed-in session, so reading this one adds nothing.

**#12 Python — `jsonpath-rfc9535` 2.0.2.** 2.0 dropped the `setup_function_extensions` hook, so the
override that removes `match()` and `search()` (backtracking regular expressions a customer could type)
silently removed nothing. `test_check_config.py` caught it; the environment now removes them from
`functions` after construction.

**#10 Go — 13 updates, gRPC-Go 1.84.** 1.84 serves a unary call as a stream, and a request it cannot
decode makes `RecvMsg` write `INTERNAL` to the client before the handler returns. The receiver's server
now uses `exportCodec` (grpccodec.go): grpc-go's own proto codec, except that an export's decode failure
is handed to the handler, which answers `INVALID_ARGUMENT` as OTLP requires. No extra copy of the body.
`TestGRPCMalformedPayloadIsNotRetryable` and `TestGRPCNewServicesRefuseAMalformedPayload`, red on 1.84,
pass.

**#11 JavaScript — 11 updates.**
- TypeScript 7: the router's error page takes `ErrorComponentProps` (a route can throw anything).
- Biome 2.6 formats Markdown and YAML by default — 204 documents; `biome.json` turns both formatters off,
  so documents keep their authors' shape. Fixed what it found: an unclosed emphasis in
  `console-redesign/spec.md`, a test pasted twice in `CheckDetailScreen.test.ts`, `Math.max` in
  `EvidenceView`. Kept, with the reason: `useMathMinMax` on timestamps compared as text (Math.max would
  return NaN), `innerText` in the browser tests (what a person sees), a test harness component made once
  per mount.
- react-virtual 3.14.22 (unpinned from 3.14.10): virtual-core now measures the scroll element by
  `clientHeight`, not `offsetHeight`; three tests faked the old one. They fake the measure it reads.
- Vitest 5, `@types/node` 26, TanStack Query and Router, OpenTelemetry API 1.10 and web SDK 2.12,
  web-vitals 6.2.3, Playwright 1.64.1: no change needed.

## Next action

The pull request; it closes #10-#12 when it merges.

## Open decisions

None. The `session.ts` alert was decided on 2026-10-30: "won't fix", keeping the behaviour.

## Blocked

Nothing.

## Risks

`test_traces_live.py` fails 4 of 21 on the development stack while the OpenTelemetry demo sends live
traffic (a summary counting fewer spans than its trace, a page repeating a trace) — on `main` too. Either
the tests read traces still being written, or summaries lag their spans; to be found out on its own.

Major versions of the compiler, test runner and formatter: their effect was the gate's to find, and it
found five things. The receiver's codec rests on `grpc.ForceServerCodecV2`, which grpc-go marks
experimental but "supported throughout 1.x".

## Validation

Recorded with each item above. **Gate, 2026-10-30:** formatting, ruff, mypy (647 files), golangci-lint,
biome, `lint-deploy`, the Go tests, web 1,373, SDK 124 (bundle under budget), contracts and chunk sizes —
all pass. Python 4,499 passed, **4 failed in `test_traces_live.py`, and fail identically on unchanged
`main`** (run from a separate copy against the same stack): they disagree with the OpenTelemetry demo's
live traffic, which CI does not have. Not this change's; recorded under *Risks* for its own investigation.

## Specification impact

None.

## Documentation impact

None beyond this record.

## Last updated

2026-10-30
