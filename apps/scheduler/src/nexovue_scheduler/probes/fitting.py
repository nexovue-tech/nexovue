"""Keeping a run's evidence small enough to publish."""

from __future__ import annotations

from typing import Final

from nexovue_platform.checks.evidence import RunEvidence

# Kafka's producer refuses a message over a megabyte. Evidence past this loses its
# bodies first, then its headers, and says so.
MAX_EVIDENCE_BYTES: Final = 512 * 1024


def fitted(evidence: RunEvidence) -> RunEvidence:
    """Evidence small enough to publish: bodies go first, then headers, and the
    run says so. The outcome, the assertions and the error always survive."""
    if len(evidence.model_dump_json()) <= MAX_EVIDENCE_BYTES:
        return evidence
    lighter = evidence.model_copy(
        update={
            "steps": [
                step.model_copy(
                    update={
                        "request": step.request.model_copy(update={"body": None})
                        if step.request
                        else None,
                        "response": step.response.model_copy(update={"body": None})
                        if step.response
                        else None,
                    }
                )
                for step in evidence.steps
            ]
        }
    )
    if len(lighter.model_dump_json()) > MAX_EVIDENCE_BYTES:
        lighter = lighter.model_copy(
            update={
                "steps": [
                    step.model_copy(
                        update={
                            "request": step.request.model_copy(update={"headers": []})
                            if step.request
                            else None,
                            "response": step.response.model_copy(update={"headers": []})
                            if step.response
                            else None,
                        }
                    )
                    for step in lighter.steps
                ]
            }
        )
    note = "evidence was too large to keep whole; bodies and headers were left out"
    error = f"{evidence.error}; {note}" if evidence.error else note
    return lighter.model_copy(update={"error": error})
