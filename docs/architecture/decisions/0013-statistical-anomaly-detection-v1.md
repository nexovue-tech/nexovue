# 13. Anomaly detection v1 is statistical, not ML

- **Status:** Accepted
- **Date:** 2026-08

## Context

Detection is the trigger for everything the product does. Starting with machine learning means
training, tuning and explaining a model before a single finding exists.

## Decision

Statistics first. Per metric: a seasonality-aware rolling baseline comparing the same hour-of-day
and day-of-week, using median+MAD or EWMA, flagging on sustained deviation (3+ consecutive
minutes). Plus absolute rules — error rate above X%, a synthetic check failing 2+ consecutive runs.
ML later, driven by observed gaps.

## Consequences

Detections are explainable and debuggable from day one, and the thresholds are ours to tune. We
will miss anomaly shapes that only a model catches; the gaps we actually observe become the
requirements for whatever replaces this.
