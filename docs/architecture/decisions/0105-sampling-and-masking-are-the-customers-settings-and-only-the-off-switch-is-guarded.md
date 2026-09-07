# 0105 — Sampling and masking are the customer's settings, and only the off-switch is guarded

- **Status:** Accepted
- **Date:** 2026-09-07
- **Applies:** [0059](0059-masking-defaults-are-high-confidence-only.md)'s defaults to the screen
  that will edit them.

## Context

`projects.sampling_config` and `projects.masking_config` are both `JSONB`, both default `'{}'`, and
**nothing in the API writes either**. A grep across `apps/api/src` finds them in `models.py` and
nowhere else, so the only way a project's masking changes today is one of us running an `UPDATE`.
`phase-1-plan.md` §9's fourth open question asks whether that is the intended end state or an
accident of ordering. It is the second, and P1-76 is the screen that forces the answer.

**The two are not equally safe, and the difference is direction rather than kind.** Sampling
changes what is kept from the next span onward, and setting it back restores the behaviour.
Masking is applied before anything downstream sees the value — `mask`'s package comment is explicit
that this is *"the first stage after decode on purpose"* — so a value masked is a value nobody can
recover, and the write that widens masking and the write that narrows it have opposite risks.

**What peers do**, checked rather than assumed. Sentry exposes scrubbing as an organisation and
project setting, on by default, with a "Use Default Scrubbers" toggle and a customer-supplied
additional-fields list ([Sentry data scrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/)).
Datadog's Sensitive Data Scanner is customer-configured rules over a shipped library
([Datadog](https://docs.datadoghq.com/security/sensitive_data_scanner/)). Neither treats redaction
as something support does on the customer's behalf, and both accept that already-stored events keep
what they were stored with.

**One hole is left over from the last time this was looked at.** `refresh.go` now keeps the
last-good masking rules when a document fails to parse, after the comment there was found to be
claiming the opposite of the truth. But on a **cold start** — a worker that has never loaded that
project — a malformed document still falls through to the defaults, and the defaults *name no
custom keys*. So the first restart after a bad edit is when a project silently stops masking the
keys it asked for. The log line says so; nothing else does.

## Decision

**Both are customer settings on the project screen, and neither is a support action.** A customer
knows which of their attribute keys carry secrets; we do not, and an arrangement where finding out
requires us to read their telemetry is worse for them than a settings form.

**The API validates the document on write, and that is where the cold-start hole closes.** A
document that the worker would reject must not be storable — the grammar is small and closed
(`disable`, `hash`, `mask_keys`, `hash_keys`, `mask_key_contains`, `hash_key_contains`,
`allow_values`, and an unknown family name is already an error), so the API can refuse it with a
422 naming the line, at the moment somebody typed it. Validating only in the worker is validating
after the row is stored, a restart away from taking effect, in a log nobody is reading.

**Turning a shipped rule off is the one guarded action.** `disable: ["credential_keys"]` widens
what leaves the customer's process and reaches our disk, and it is the only edit here whose blast
radius is other people's secrets. It gets an explicit confirmation that names the rule and an audit
row saying who and when. Everything else — sampling rates, added `mask_keys`, `allow_values` — is
an ordinary form field.

**Sampling gets no guard at all.** It is reversible, it changes only what is kept next, and adding
a confirmation dialogue to a reversible setting teaches people to click through the one that matters.

## Alternatives considered

**Keep both as support actions.** It does not scale past the first ten customers, it makes a
one-line change a support ticket, and it requires us to look at the customer's attribute keys to
help them — which is the thing masking exists to avoid.

**Masking read-only, sampling editable.** The tempting middle, and it fails on who has the
knowledge: the customer is the only party who knows that their `x-internal-trace` header carries a
session. Read-only masking means every real use of it goes through us anyway.

**Validate in the worker only, and surface the rejection on the screen.** The rejection is
asynchronous, the row is already stored, and between the write and the next refresh the project
believes it is masking. A 422 at write time is the same check moved to where the person is.

**A regex-based rule builder, as Datadog has.** Deliberately not: `mask`'s document carries whole
values and key substrings and no patterns, for reasons ADR 0059 records, and a regex a customer
typed is a regex that runs on the ingest path.

## Consequences

**One grammar, two parsers.** The API validator is Python and the enforcer is Go, and neither can
import the other. That is precisely the shape `test_cross_service_contracts.py` was written for,
and the family names and document keys have to be asserted equal there — an API that accepts a
family the worker rejects is the cold-start hole with a nicer form on top.

**Masking still cannot recover what is already written**, and the screen has to say so rather than
implying that turning a rule on cleans up history. Sentry's own documentation reaches for deleting
and recreating the project; ours should at least not pretend.

**An audit row is a new obligation.** There is no audit table today, so the guarded off-switch adds
one — small, and worth naming as a cost rather than discovering it during P1-76.
