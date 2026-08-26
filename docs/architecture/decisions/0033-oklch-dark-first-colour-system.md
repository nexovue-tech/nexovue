# 33. Colour system — OKLCH, dark-first, and health is the absence of colour

- **Status:** Accepted — contrast method amended by [0041](0041-colour-verified-against-apca-and-wcag.md)
- **Date:** 2026-08-25

## Context

An observability UI is looked at for hours, mostly when something is wrong, and its colour has to
carry meaning rather than decoration. Severity colour also collides with the most common colour
blindness, and a product whose selling point is calm cannot fill a screen with alarm.

## Decision

OKLCH for perceptual uniformity. Separate light and dark palettes, dark-first, twelve steps each
with a defined job. Body text at APCA Lc ≥ 60.

The governing rule: **status hues are reserved and excluded from the categorical series palette.**
If a chart series can be red, red stops meaning "bad".

Severity is critical=red, warning=amber, info=blue, **healthy=neutral with no hue** — colour means
something needs you, and a wall of green dots contradicts a product whose point is calm. This also
sidesteps the red/green deuteranopia collision without abandoning red, which is too deeply learned
to fight.

Colour is never the sole carrier: every severity also has shape, weight and position.

Full system in `../../design/design-system.md`.

## Consequences

A healthy system is visually quiet, and anything coloured is worth looking at. The cost is a
permanent constraint on chart design: the categorical palette is smaller than it could be, and any
new chart type has to work within it. Contrast verification was later widened from APCA alone —
see [0041](0041-colour-verified-against-apca-and-wcag.md).
