# Bug: a token *count* was masked as if it were a token *credential*

Found 2026-10-09 while seeding `gen_ai.*` fixtures for F29 (LLM observability) through the real
ingest path.

## Reported behavior

A seeded span carrying `gen_ai.usage.input_tokens = 842` and `gen_ai.usage.output_tokens = 156`
came back from ClickHouse with both values replaced by the masking placeholder:

```
input_tokens:  [masked]
output_tokens: [masked]
```

## Reproduction

1. `go run ./tools/gateseed` sends a span with `gen_ai.usage.input_tokens`/`output_tokens`
   attributes through the real receiver, as a customer's SDK would.
2. Queried back: `toString(getSubcolumn(span_attrs, 'gen_ai.usage.input_tokens'))` on the matching
   row returned `[masked]`, not `842`.
3. Isolated to the masking stage, not the receiver or the query: `apps/workers/internal/pipeline/
   mask/rules.go`'s `credentialKeyMarkers` list contains the bare string `"token"`, matched by
   `containsAny` — a plain `strings.Contains`, with no word boundary and no check on the
   attribute's value type.

## What was ruled out

- **Not a receiver or query bug.** The same `getSubcolumn` technique already reads other attributes
  correctly on the same row (`gen_ai.provider.name` came back intact on the same span).
- **Not specific to this attribute, or to gen_ai.** `containsAny(lowered, credentialKeyMarkers)`
  matches "token" as a substring of any key, regardless of what else is in the key or what type the
  value is — confirmed by reading `maskAttributes`, which calls `rules.masksKey(attribute.GetKey())`
  before ever inspecting `attribute.GetValue()`'s type.

## Root cause

`apps/workers/internal/pipeline/mask/rules.go`, `credentialKeyMarkers` and `masksKey`. The
credential-keys family — on by default, matching `password`/`secret`/`token`/`api_key` substrings —
treated `"token"` as a bare substring with no word boundary. `gen_ai.usage.input_tokens` and
`output_tokens` both contain that substring, so every LLM token count was masked as though it were
an authentication token, unconditionally, with no value-type check that might have spared a numeric
field. The rule's intent (`api_token`, `session_token`, `access_token`, `refresh_token` — all
singular) was never actually expressed in the match; a bare `strings.Contains` cannot distinguish
"ends in a credential" from "ends in a count of them."

## Specification impact

None directly — F29's spec (`docs/features/llm-observability/spec.md`) already named this exact
risk class in its *Non-functional requirements* (tolerance for the convention renaming a field) and
*Out of scope* (no frozen metric contract), but had not anticipated masking destroying the value
before any contract question could even arise. No spec text needs to change; the fix makes the
spec's premise — that the data lands and is readable — actually true.

## Regression test

- `apps/workers/internal/pipeline/mask/token_falsepositive_test.go` (new, mirroring the existing
  `card_falsepositive_test.go` pattern): `TestTokenCountsAreNotMistakenForCredentials` asserts
  `gen_ai.usage.input_tokens`, `output_tokens`, `rate_limit.tokens_remaining`, `max_tokens`, and bare
  `tokens` are never matched; `TestRealTokenCredentialsAreStillMasked` asserts `token`,
  `access_token`, `session_token`, `api_token`, `refresh_token`, `auth.access_token`,
  `token_signature`, and the unrealistic-but-not-ruled-out `tokens_token` (a genuine singular
  occurrence after a plural one) are all still matched.
- `apps/workers/internal/pipeline/mask/mask_test.go`'s `TestDefaultRulesLeaveOrdinaryTelemetryAlone`
  gained `gen_ai.usage.input_tokens`/`output_tokens` cases, exercising the full `Masker` pipeline,
  not just the matching helper in isolation.
- **Observed failing before the fix**: reverted only the production line
  (`masksKey`'s call site, restoring the old bare `containsAny(..., "token")` behavior) and re-ran —
  `TestDefaultRulesLeaveOrdinaryTelemetryAlone/gen_ai_input_token_count` and `/gen_ai_output_token_count`
  both failed: `value for "gen_ai.usage.input_tokens" became "[masked]", want it untouched ("842")`.
- **Observed passing after**: full `mask` package, all green, including the existing `"token key":
  {"auth.access_token", ...}` case in `TestDefaultRulesMaskWhatCanNeverBeNeededForDebugging` — proving
  the fix narrows, rather than removes, the rule's reach.
- **Observed live, against the real stack**: worker restarted (air rebuilt it with the fix),
  `gateseed` re-run, the same fixture spans re-queried from ClickHouse: `input_tokens: 842`,
  `output_tokens: 156`, `310`, `48` — real numbers, not the placeholder.

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.
