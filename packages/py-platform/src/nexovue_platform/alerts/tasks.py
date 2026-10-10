"""The alert tasks' names and queue: the contract between whoever dispatches one —
beat, the task that records a check's state, the API's send test — and the worker.

**Deliveries have a queue and a worker of their own** (design, *Data flow*; plan
Q1): a receiver that takes its full ten seconds to answer must hold a delivery
slot, never a probe's. Not `light.deliveries`: `light.<name>` is where a probe
location's runs are dispatched, and an operator naming a location `deliveries`
would have its probes and our alerts share a queue.
"""

from __future__ import annotations

from typing import Final

DELIVERIES_QUEUE: Final = "deliveries"

DELIVER_TASK: Final = "nexovue_scheduler.alerts.deliver"
SWEEP_DELIVERIES_TASK: Final = "nexovue_scheduler.alerts.sweep_deliveries"
PRUNE_ATTEMPTS_TASK: Final = "nexovue_scheduler.alerts.prune_attempts"
# The minute-by-minute evaluations of the service conditions (ADR-012). On the
# shared `light` queue: they read and decide, and delivery is someone else's.
EVALUATE_THRESHOLDS_TASK: Final = "nexovue_scheduler.alerts.evaluate_thresholds"
EVALUATE_SILENCE_TASK: Final = "nexovue_scheduler.alerts.evaluate_silence"
EVALUATE_NEW_ERRORS_TASK: Final = "nexovue_scheduler.alerts.evaluate_new_errors"
EVALUATIONS: Final = ("thresholds", "silence", "new_errors")


# Dispatched by the API, by name, as Run now is (ADR-011): the API imports no
# scheduler code, and `test_cross_service_contracts.py` holds the arguments.
SEND_TEST_TASK: Final = "nexovue_scheduler.alerts.send_test"

# How long a send test's outcome waits to be collected. The API waits ten seconds
# for it; the rest is room for a console that asks again.
SEND_TEST_RESULT_TTL_SECONDS: Final = 300
# What a send test's outcome is filed under in `job_results`.
SEND_TEST_RESULT_KIND: Final = "channel_test"


def send_test_arguments(*, org_id: str, channel_id: str, request_id: str) -> dict[str, str]:
    """What the API sends and the worker accepts, spelled once."""
    return {"org_id": org_id, "channel_id": channel_id, "request_id": request_id}
