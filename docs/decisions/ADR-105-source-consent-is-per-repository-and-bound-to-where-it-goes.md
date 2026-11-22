# ADR-105: Source consent is per repository, bound to where it goes

## Status

Accepted with F5's design, approved by the founder on 2026-11-02 (design, *Proposed ADRs*,
third). Filed 2026-11-22 with F5 Step 9 (plan T9.9), as built in Steps 3 to 5. Awaiting the
founder's review with Step 9's diff.

## Context

Turning AI on (F3) covers telemetry facts. Source code is a larger disclosure, and an admin
could repoint the provider, or swap its key for one in their own account, after an owner agreed.

## Decision

1. **An owner agrees per repository and folder** (`fix_source_consents`, migration 0079). One
   live consent per repository and folder.
2. **The consent names where the code goes:** the provider's full address, wire format, key id
   and model, compared at every use (the click, the pack job, the AI job). A different provider,
   a new key or another model means asking the owner again; on a gateway the model can decide
   which company receives the code (F3, BR12).
3. **Every way access ends deletes stored code:** an owner's withdrawal, a disconnect, AI off,
   GitHub access gone for 72 hours, a deleted project (ADR-094), a restore. A consent that ends
   by itself (a changed connection) stops new source only, and every owner is told by email
   (`fixes/notify.py`).

## Consequences

More setup for a customer with many repositories. Nothing can quietly widen. A key rotation
stops new source until an owner agrees again and leaves stored suggestions alone. A restore
withdraws every consent, so owners agree again after one (AC6a, checked per release by
`test_fix_restore_docker.py`).
