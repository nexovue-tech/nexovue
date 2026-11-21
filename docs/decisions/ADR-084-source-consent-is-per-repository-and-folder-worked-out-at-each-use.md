# ADR-084: Source consent is per repository and folder, and worked out at each use

## Status

Accepted 2026-11-21: proposed with the F5 design (2026-11-02, *Proposed ADRs*, third) and approved with
it; filed with F5 Step 3.

## Context

Turning AI on (F3) covers telemetry facts. A repository's code is a larger disclosure, and an admin could
repoint the provider, swap its key for one in another account, or, on a gateway, change the model and with
it the company that receives the code.

## Decision

1. **An owner agrees per repository and folder** (`fix_source_consents`; F3's `CONSENT_AI_DISCLOSURE`), so a
   monorepo may agree to `services/a` and not `services/b`. The owner must answer, with nothing preselected,
   whether quoted code needs a linked GitHub account with read access (D20).
2. **A consent records the provider's full address, wire format, key id and model, and is live only while
   all four equal the AI connection's now** (`nexovue_platform.fixes.consent`). It is compared at Ask, at the
   pack job and at Send; **no statement ever ends a consent by writing to it**, so no path can forget (AC9).
   Key rotation keeps the key id, so it does not end consent; a new key does.
3. **Withdrawing ends the work it covered and deletes its stored code** in the same transaction, through the
   one statement (`clear_stored_code`); consent ending by itself stops new source only (D43).

## Consequences

Many-repository customers have more to agree to. F3 ends its own consent first on a new address, key or
format, so those changes surface as `ai_off`; a model change surfaces as `no_consent`. Either way the next
request stops.
