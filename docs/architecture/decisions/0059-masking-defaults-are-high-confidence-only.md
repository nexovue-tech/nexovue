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
