# 41. Colour verified against APCA *and* WCAG 2.1 AA

- **Status:** Accepted
- **Supersedes in part:** [0033](0033-oklch-dark-first-colour-system.md) — its APCA-only contrast target
- **Date:** 2026-08-26

## Context

[0033](0033-oklch-dark-first-colour-system.md) verified contrast with APCA alone. APCA belongs to
WCAG 3, which is still developmental — "WCAG 3 Compliant does not exist yet" — while WCAG 2.1 AA is
what enterprise procurement and any VPAT will actually ask for.

Tuning to APCA alone produced a palette failing WCAG 2.1 in six places, all in the light theme.

## Decision

Verify against both standards. Severity badges are restructured as subtle fill + coloured label +
border, because saturated mid-lightness fills sit in a dead zone where neither white nor black text
passes 4.5:1. 28 pairs now clear both standards.

## Consequences

The palette survives a procurement accessibility review, not only a perceptual one. Cost: every
future colour pair has to clear two standards that disagree, and badges gained structure they would
not otherwise need.
