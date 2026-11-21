"""The connection test, "Test this connection" (spec FR24, FR27): a constant body holding no
telemetry (AC35).

The same bytes for every organization and every test, so the run list can rebuild what a
test sent without any stored content, and a test proves only that the address, key and model
answer.
"""

from __future__ import annotations

from typing import Final

from nexovue_platform.ai.contract import AIRequest, Trusted

CHECK_PROMPT_VERSION: Final = "check-1"
# Enough for `{"ok":true}` and little more: a test should cost almost nothing (A51).
CHECK_MAX_OUTPUT_TOKENS: Final = 16

CHECK_REQUEST: Final = AIRequest(
    instructions='Answer with the JSON object {"ok": true} and nothing else.',
    parts=(Trusted("This is a connection test from nexovue. It holds no data."),),
    response_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    },
    schema_name="connection_test",
    max_output_tokens=CHECK_MAX_OUTPUT_TOKENS,
    purpose="check",
)
