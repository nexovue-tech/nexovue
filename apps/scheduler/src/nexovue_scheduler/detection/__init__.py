"""F6: anomaly detection — judging each service's settled minutes against its own normal.

`settle` decides which minute every part of the pipeline has finished writing.
`store` is detection's only way into the telemetry store: reads bound to one
project, and the one writer of `detection_readings` (ADR-023).
`method` decides whether a window is unusual; `baselines` builds each hour's
normal; `lifecycle` decides what each judged minute does to a finding;
`project_job` judges one project's settled minutes and keeps its findings;
`notify` decides who is told about a finding, and about detection stopping;
`coordinator` hands out the project jobs each minute.
"""

from __future__ import annotations

from typing import Final

# Detection's jobs have a queue of their own, taken by `scheduler-light` beside
# `light` and capped there, so judging never holds every probe's slot (C23, D32).
DETECTION_QUEUE: Final = "detection"
# At most this many detection jobs at once in one worker, of its eight slots.
DETECTION_CONCURRENCY: Final = 2

# Named here rather than imported from the modules that run them, so beat
# schedules them without loading the store's client.
SWEEP_DETECTION_TASK: Final = "nexovue_scheduler.detection.sweep"
JUDGE_PROJECT_TASK: Final = "nexovue_scheduler.detection.judge_project"
SWEEP_BASELINES_TASK: Final = "nexovue_scheduler.detection.sweep_baselines"
COMPUTE_BASELINES_TASK: Final = "nexovue_scheduler.detection.compute_baselines"
