# 0059 — Masking is on by default only for what can never be needed

- **Status:** Accepted
- **Date:** 2026-08-27

## Context

P1-24 puts a masking step in the workers with rules configurable in Postgres. Masking runs before
the write and is irreversible: once a value is masked the original is gone, and no support
conversation can recover it.

That makes the default configuration a real decision rather than a starting point. Two failure modes
sit on either side. Ship no default rules and the out-of-the-box state is the leaky one — and the
customers most likely to put a bearer token in a span attribute are the least likely to configure
masking before they do it. Ship aggressive defaults and the product is worse for everyone: *which
user hit this* is a normal debugging question, and emails and IP addresses are how it gets answered.

## Decision

**On by default only for values that can never be legitimately needed for debugging**, and that are
cheap to detect with few false positives:

- `authorization` headers and cookies
- card numbers passing a Luhn check
- any attribute whose **key** matches password, secret, token or api_key

Everything else — emails, IP addresses, phone numbers, names — is **opt-in**, configurable per
project in Postgres.

The bar for a default rule is that sentence: not "is this sensitive" but "could a developer ever
legitimately need this to debug". A bearer token fails that test. An email address does not.

## Consequences

The default protects against the accidents that actually happen — a token in a header, a card
number in a query string — without silently destroying the attributes people investigate with. A
customer who needs stricter handling configures it; a customer who never thinks about it is still
covered for the worst cases.

Accepted: this is not GDPR compliance out of the box, and it should not be described as such. A
customer with genuine personal-data obligations must configure the rules for their own data, and
the product's job is to make that easy and to say plainly that the defaults do not do it for them.

Accepted: key-based matching misses a secret in a value under an innocuous key, and Luhn produces
occasional false positives on long digit strings. Both are the right trade at this bar — the
alternative is content scanning every attribute on the hot path, which costs more than it protects.

## Amended 2026-08-28 — `authorization_credentials` joins the defaults

The four rules above cover keys and card numbers. They leave the commonest leak
in a log untouched: a bearer token printed inside a message. A client logs the
request it is about to make, or an error handler prints the headers it had, and
the credential lands in free text where no key rule can see it.

Every current write-up on log hygiene puts API tokens in error messages at the
top of its risk list, and until now a project could not even opt in — every
project-level knob here is key-based, deliberately, because a regex loaded from
the database and run over every value is a performance cliff and a
denial-of-service vector wearing a configuration field's costume.

The rule is **anchored on the scheme word**, not on what the value looks like:
the run of token characters after `Bearer ` or `Basic `. That is what lets it be
on by default. A credential following an authorization scheme is a credential by
definition, so there is no judgement to get wrong — and it clears this record's
bar, since a developer can never legitimately need a bearer token's value to
debug.

**A generic high-entropy rule was rejected.** It would fire on trace ids, build
hashes and base64 payloads, and this project has already had a value rule eat
ordinary data: the card rule masked a Luhn-passing nanosecond timestamp and
mangled a service name, which broke a chaos run. The false-positive guard here
is a test of ordinary prose that mentions a scheme — *"bearer token missing"*,
*"Basic auth failed"* — asserting the text comes back byte-identical.

A minimum credential length of twenty keeps it off those phrases; published
detection patterns settle on the same floor.
