"""F3: a finding's facts, gathered with no AI code in the path (spec BR3; design, *Facts:
when and how*).

`store` reads the telemetry store and `reads` Postgres; `rules` and `sentences` are pure;
`inputs` builds what a model may be sent, with no AI code; `gather` is the task. Nothing
here imports `nexovue_platform.ai` or `nexovue_scheduler.insight` (AC5,
`test_detection_has_no_ai.py`), nor the probes' HTTP client.
"""
