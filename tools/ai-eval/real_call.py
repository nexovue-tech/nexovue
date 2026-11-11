"""One real call through one wire format, and the dated record it earns (F3 plan T4.10; AC77).

A format is offered on `/settings/ai` only after it has completed one real call that reported
token counts (`nexovue_platform.ai.real_calls.REAL_CALLS`). This sends the constant connection
test, which holds no telemetry, through the same transport the AI role uses, and prints the
line to add to that record by hand, in a reviewed change.

**Each run is a real, metered call** on the account whose key is given. Synthetic data only:
the request is the constant test. The key is read from `AI_EVAL_KEY` and never printed.

    AI_EVAL_KEY=... uv run python tools/ai-eval/real_call.py \\
        --format openai_chat --base-url https://generativelanguage.googleapis.com/v1beta/openai \\
        --model gemini-2.5-flash --provider Gemini

A local server needs its address listed, as the operator would: `--private 192.168.1.20:11434`.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from typing import Final

from nexovue_platform.ai.check_prompt import CHECK_REQUEST
from nexovue_platform.ai.connection import ConnectionRefused, check_connection, check_key
from nexovue_platform.ai.contract import AIResponse
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai_records import WIRE_FORMATS

from nexovue_scheduler.insight import transport
from nexovue_scheduler.probes.addresses import PrivateEndpoints

KEY_VARIABLE: Final = "AI_EVAL_KEY"
CALL_SECONDS: Final = 60


def call_once(
    *,
    format_name: str,
    base_url: str,
    model: str,
    key: str | None,
    private: str = "",
    send: transport.Send = transport.send,
) -> AIResponse | str:
    """The provider's answer to the constant test, or why no call was made or answered."""
    try:
        check_connection(wire_format=format_name, base_url=base_url, model=model, region=None)
        if key:
            check_key(key)
    except ConnectionRefused as refused:
        return refused.reason
    fmt = wire_format(format_name)
    call = fmt.build(CHECK_REQUEST, model=model, base_url=base_url, key=key)
    answer = send(
        call, timeout_seconds=CALL_SECONDS, private_endpoints=PrivateEndpoints.parse(private)
    )
    if answer.failure is not None:
        return answer.failure
    return fmt.parse(answer.status or 0, answer.body)


def record_line(
    *, format_name: str, provider: str, model: str, response: AIResponse, on: date
) -> str | None:
    """The `RealCall(...)` to add, only for a call that answered and reported its counts: an
    estimated count proves nothing about the format's usage fields."""
    reported = response.input_tokens is not None and response.output_tokens is not None
    if response.outcome != "ok" or not reported or response.tokens_estimated:
        return None
    return (
        f"RealCall({format_name!r}, date({on.year}, {on.month}, {on.day}), {provider!r}, "
        f"{model!r}, {response.input_tokens}, {response.output_tokens}),"
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--format", required=True, choices=WIRE_FORMATS, dest="format_name")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True, help="Who answered, as the record says it")
    parser.add_argument("--private", default="", help="AI_PRIVATE_ENDPOINTS for a local server")
    parsed = parser.parse_args(arguments)
    result = call_once(
        format_name=parsed.format_name,
        base_url=parsed.base_url,
        model=parsed.model,
        key=os.environ.get(KEY_VARIABLE) or None,
        private=parsed.private,
    )
    if isinstance(result, str):
        print(f"no answer: {result}", file=sys.stderr)
        return 1
    line = record_line(
        format_name=parsed.format_name,
        provider=parsed.provider,
        model=parsed.model,
        response=result,
        on=datetime.now(UTC).date(),
    )
    if line is None:
        print(f"no record: the call ended {result.outcome}, or reported no counts", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
